package com.printtheshot.printer;

import android.annotation.SuppressLint;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothManager;
import android.bluetooth.BluetoothSocket;
import android.content.Context;
import android.content.SharedPreferences;
import android.os.Build;
import android.util.Log;

import java.io.IOException;
import java.io.OutputStream;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * 经典蓝牙 SPP 打印连接管理器 / Classic Bluetooth SPP printing connection manager
 * ==============================================================================
 *
 * 中文
 * ----
 * 一个进程内只应该有一条打印连接,所以这里做成单例({@link #get()}),由
 * {@link PrintTheShotPrinterPlugin}(JS 侧)、{@link BluetoothPrinterBridge}
 * (Python / pyjnius 侧)和 {@link LocalPrintBridge}(本机 HTTP 桥)共用同一份
 * socket,避免三方各连一条把打印机连爆 —— 绝大多数热敏机同时只接受一条
 * SPP 连接。
 *
 * 线程模型 / threading:
 *   - 所有会阻塞的蓝牙操作(连接、写数据、断开)都必须在 {@link #executor()}
 *     这条**单线程**工作队列上跑,既避免阻塞 UI 线程,也天然把「同时写」排队
 *     成串行。调用这些方法前请先确认自己在工作线程上。
 *   - 主线程只读 {@link #isConnected()} 这类状态方法,它们不会碰 socket。
 *   - blocking Bluetooth work (connect / write / disconnect) must run on the
 *     single-threaded worker queue from {@link #executor()}: that keeps the UI
 *     thread free and serialises concurrent writes by construction. State-only
 *     readers such as {@link #isConnected()} are safe from the main thread.
 *
 * 长连接与重连 / long-lived connection and reconnection:
 *   连接成功后会一直留着,后续每次打印复用同一个 socket。打印前会自动检查
 *   连接,断了就按上次的地址重连(最多 {@link #MAX_RECONNECT_ATTEMPTS} 次)。
 *   但**一份任务写到一半断线不会自动重打** —— 那样会吐出一张半截重复的小票,
 *   宁可报错让用户从小票界面重新点一次打印。
 *   The socket is kept open and reused for every job. Before each job the
 *   connection is checked and re-established against the last address (up to
 *   {@link #MAX_RECONNECT_ATTEMPTS} times). A job that breaks mid-write is
 *   **never** silently retried: that would feed a half-duplicated receipt.
 *   Failing loudly and letting the user press print again is the better
 *   trade-off.
 *
 * 分块发送 / chunked transfer:
 *   绝大多数小票机蓝牙缓冲区只有几百字节到几 KB,一次 write 塞进几百 KB 的
 *   位图必然溢出丢数据。所以每 {@link #CHUNK_SIZE} 字节写一次并 flush,块间
 *   停 {@link #CHUNK_GAP_MS} 毫秒等打印机消化。
 *   Most receipt printers have a Bluetooth buffer of a few hundred bytes to a
 *   few KB; dumping a multi-hundred-KB bitmap in one write will overflow it and
 *   lose data. So data goes out in {@link #CHUNK_SIZE} byte chunks, flushed and
 *   separated by {@link #CHUNK_GAP_MS} ms to let the printer drain.
 *
 * English
 * -------
 * There should be exactly one printing connection per process, so this is a
 * singleton ({@link #get()}) shared by {@link PrintTheShotPrinterPlugin} (the
 * JS side), {@link BluetoothPrinterBridge} (the Python / pyjnius side) and
 * {@link LocalPrintBridge} (the on-device HTTP bridge). Three separate sockets
 * would fight over the printer, and virtually every thermal unit accepts only
 * one SPP connection at a time.
 */
public final class BluetoothPrinter {

    /** 日志标签 / log tag. */
    static final String TAG = "PrintTheShot";

    /**
     * 经典蓝牙 SPP(串口)服务 UUID / Classic Bluetooth SPP (serial port) UUID.
     *
     * 00001101-0000-1000-8000-00805F9B34FB 是蓝牙 SIG 定义的 Serial Port
     * Profile,热敏小票机的标准通道 —— 换别的 UUID 基本连不上。
     * This is the Bluetooth SIG Serial Port Profile — the standard channel for
     * thermal receipt printers; anything else generally will not connect.
     */
    public static final UUID SPP_UUID =
            UUID.fromString("00001101-0000-1000-8000-00805F9B34FB");

    /** 每次蓝牙写的数据块大小(字节)/ bytes per Bluetooth write. */
    private static final int CHUNK_SIZE = 512;

    /** 数据块之间的间隔(毫秒)/ pause between chunks in milliseconds. */
    private static final long CHUNK_GAP_MS = 20L;

    /** 连接超时(毫秒)/ connect timeout in milliseconds. */
    private static final long CONNECT_TIMEOUT_MS = 12000L;

    /** 连上以后等打印机就绪的时间(毫秒)/ settle time after connect, ms. */
    private static final long CONNECT_SETTLE_MS = 300L;

    /** 打印前最多重连几次 / reconnect attempts before a job. */
    private static final int MAX_RECONNECT_ATTEMPTS = 2;

    /** 一次打印任务的超时基数(毫秒)/ base timeout for one print job, ms. */
    private static final long BASE_PRINT_TIMEOUT_MS = 30000L;

    /** 每块数据额外预留的时间(毫秒)/ extra budget per chunk, ms. */
    private static final long PER_CHUNK_BUDGET_MS = 30L;

    /** 单线程工作队列 / the single-threaded worker queue. */
    private static final ExecutorService WORKER = Executors.newSingleThreadExecutor(new ThreadFactory() {
        @Override
        public Thread newThread(Runnable r) {
            Thread t = new Thread(r, "printtheshot-bt");
            // 守护线程:进程退出时不拖着不退 / daemon so it never blocks exit
            t.setDaemon(true);
            return t;
        }
    });

    /** 首选项文件名 / SharedPreferences file name. */
    private static final String PREFS = "printtheshot";

    /** 上次连接成功的打印机地址 / address of the last successful connection. */
    private static final String KEY_DEFAULT_ADDRESS = "default_printer_address";

    private static final BluetoothPrinter INSTANCE = new BluetoothPrinter();

    /** 取单例 / obtain the singleton. */
    public static BluetoothPrinter get() {
        return INSTANCE;
    }

    private BluetoothPrinter() {
    }

    /** 应用上下文 / application context. */
    private Context appContext;

    /** 当前 socket 与输出流 / current socket and output stream. */
    private BluetoothSocket socket;
    private OutputStream outputStream;

    /** 当前连接的地址,未连接时为 null / address of the live connection, null when idle. */
    private volatile String connectedAddress;

    /** 连接状态,供主线程读取 / connection flag, safe to read from the main thread. */
    private volatile boolean connected;

    /** 最近一次错误,给上层拼出错信息 / last error, surfaced to callers. */
    private volatile String lastError = "";

    // ------------------------------------------------------------------
    // 初始化 / initialisation
    // ------------------------------------------------------------------

    /**
     * 注入应用上下文 / inject the application context.
     *
     * 幂等,可重复调用(Activity 重建、插件 load 都会调)。只保存
     * getApplicationContext(),不持有 Activity,免得泄漏。
     * Idempotent and safe to call repeatedly (activity recreation, plugin
     * load). Only the application context is stored — never an activity — so
     * nothing leaks.
     */
    public void init(Context context) {
        if (context != null) {
            this.appContext = context.getApplicationContext();
        }
    }

    /** 上下文是否已注入 / whether a context has been injected. */
    public boolean isReady() {
        return appContext != null;
    }

    /** 工作队列,调用方用它把阻塞操作挪出主线程 / worker queue for blocking work. */
    public ExecutorService executor() {
        return WORKER;
    }

    /**
     * 把任务丢到工作队列并等结果 / run on the worker queue and wait for the result.
     *
     * 给 pyjnius 这类「必须同步拿返回值」的调用方用。**不要在 UI 线程上调**,
     * 会 ANR。
     * For callers that must have a synchronous return value, such as pyjnius.
     * **Never call this from the UI thread** — it will ANR.
     */
    public <T> T submitAndWait(Callable<T> task, long timeoutMs) throws Exception {
        Future<T> future = WORKER.submit(task);
        return future.get(timeoutMs, TimeUnit.MILLISECONDS);
    }

    /**
     * 按数据量估算打印超时 / estimate a print timeout from the payload size.
     *
     * 位图任务动辄几百 KB,按「连接 + 分块发送 + 打印机消化」算个宽松预算。
     * 给同步等待的调用方(pyjnius、HTTP 桥)用,异步的 JS 路径不需要。
     * A bitmap job easily runs to hundreds of KB, so this is a generous budget
     * covering connect, chunked transfer and the printer draining. Meant for
     * callers that wait synchronously (pyjnius, the HTTP bridge); the async JS
     * path does not need it.
     */
    public static long timeoutForPayload(int payloadBytes) {
        long chunks = (payloadBytes / (long) CHUNK_SIZE) + 1L;
        return BASE_PRINT_TIMEOUT_MS + chunks * PER_CHUNK_BUDGET_MS;
    }

    // ------------------------------------------------------------------
    // 打印机枚举 / printer enumeration
    // ------------------------------------------------------------------

    /**
     * 已配对(已绑定)的蓝牙设备 / Bluetooth devices already bonded (paired).
     *
     * 只列已配对的设备,不做蓝牙扫描 —— 经典 SPP 小票机几乎都需要先完成配对,
     * 而且扫描要额外申请定位/扫描权限、耗时十几秒,对「打开设置页选一台机器」
     * 这个场景没有收益。没配对的机器请先在系统设置里配对,再回到 App 里刷新。
     * Only bonded devices are listed, with no discovery: classic SPP receipt
     * printers effectively require bonding first, and a discovery sweep costs
     * extra location/scan permissions plus ten-odd seconds, which buys nothing
     * for "pick a printer on the settings page". Pair the device in system
     * settings first, then refresh here.
     *
     * 必须在工作线程调用 / must be called on a worker thread.
     */
    public List<PrinterEntry> listPrinters() {
        List<PrinterEntry> result = new ArrayList<>();
        BluetoothAdapter adapter = getAdapter();
        if (adapter == null) {
            setError("此设备没有蓝牙适配器 / no Bluetooth adapter on this device");
            return result;
        }
        if (!adapter.isEnabled()) {
            setError("蓝牙未开启 / Bluetooth is turned off");
            return result;
        }

        Set<BluetoothDevice> bonded;
        try {
            bonded = adapter.getBondedDevices();
        } catch (SecurityException e) {
            // Android 12+ 没给 BLUETOOTH_CONNECT 就会走到这里 /
            // Android 12+ throws here without BLUETOOTH_CONNECT.
            setError("缺少蓝牙权限(BLUETOOTH_CONNECT)/ missing BLUETOOTH_CONNECT permission");
            return result;
        }
        if (bonded == null) {
            return result;
        }

        String preferred = getDefaultAddress();
        for (BluetoothDevice device : bonded) {
            if (device == null) {
                continue;
            }
            String address = safeAddress(device);
            if (address == null) {
                continue;
            }
            String name = safeName(device);
            boolean isDefault = preferred != null && preferred.equalsIgnoreCase(address);
            result.add(new PrinterEntry(address, name, true, isDefault));
        }

        // 默认机排最前,其余按名字 / default first, then by name
        Collections.sort(result, new Comparator<PrinterEntry>() {
            @Override
            public int compare(PrinterEntry a, PrinterEntry b) {
                if (a.isDefault != b.isDefault) {
                    return a.isDefault ? -1 : 1;
                }
                return a.displayName().compareToIgnoreCase(b.displayName());
            }
        });
        return result;
    }

    // ------------------------------------------------------------------
    // 连接 / connection
    // ------------------------------------------------------------------

    /**
     * 连接到指定地址 / connect to the given address.
     *
     * 已连同一台机器时直接返回成功;连别的机器会先断开旧的。
     * Returns success immediately when already connected to the same device;
     * connecting elsewhere tears the old connection down first.
     *
     * 必须在工作线程调用 / must be called on a worker thread.
     */
    public boolean connect(String address) {
        if (address == null || address.trim().isEmpty()) {
            setError("没有指定打印机地址 / no printer address given");
            return false;
        }
        String target = address.trim();
        if (connected && target.equalsIgnoreCase(connectedAddress)) {
            return true;
        }
        disconnect();

        if (!BluetoothAdapter.checkBluetoothAddress(target)) {
            setError("蓝牙地址格式不对 / malformed Bluetooth address: " + target);
            return false;
        }

        BluetoothAdapter adapter = getAdapter();
        if (adapter == null) {
            setError("此设备没有蓝牙适配器 / no Bluetooth adapter on this device");
            return false;
        }
        if (!adapter.isEnabled()) {
            setError("蓝牙未开启,请先打开蓝牙 / Bluetooth is off, please turn it on");
            return false;
        }

        // 扫描期间连接会明显变慢甚至失败 / discovery slows connections down a lot
        try {
            adapter.cancelDiscovery();
        } catch (SecurityException e) {
            // API 31+ 需要 BLUETOOTH_SCAN;没给就忽略,后面 connect 会给出真实原因
            // Needs BLUETOOTH_SCAN on API 31+; ignore, the connect below will
            // report the real problem.
            Log.w(TAG, "cancelDiscovery 被拒 / denied: " + e.getMessage());
        }

        BluetoothDevice device;
        try {
            device = adapter.getRemoteDevice(target);
        } catch (IllegalArgumentException e) {
            setError("找不到该蓝牙设备 / unknown Bluetooth device: " + target);
            return false;
        }

        // 先试不安全 RFCOMM:不弹配对框、不要求链路加密,兼容性最好 ——
        // 不少小票机完成不了安全 RFCOMM 的链路协商,会直接连不上。
        // Try insecure RFCOMM first: no pairing prompt, no link encryption
        // requirement, and the widest compatibility — a fair number of receipt
        // printers cannot do the secure-mode key negotiation at all.
        boolean ok = openAndBind(device, false);
        if (!ok) {
            Log.w(TAG, "不安全 RFCOMM 失败,改试安全 RFCOMM / insecure RFCOMM failed, retrying secure");
            ok = openAndBind(device, true);
        }
        if (!ok) {
            return false;
        }

        connected = true;
        connectedAddress = target;
        setDefaultAddress(target);

        // 刚连上别立刻灌数据 / give the printer a moment before the first byte
        sleepQuietly(CONNECT_SETTLE_MS);
        Log.i(TAG, "已连接 / connected: " + target);
        return true;
    }

    /**
     * 打印前确保连接可用,必要时重连 / make sure the link is up before a job.
     *
     * 必须在工作线程调用 / must be called on a worker thread.
     */
    public boolean ensureConnected() {
        if (connected && outputStream != null) {
            return true;
        }
        String target = connectedAddress != null ? connectedAddress : getDefaultAddress();
        if (target == null || target.isEmpty()) {
            setError("尚未选择打印机 / no printer selected yet");
            return false;
        }
        for (int attempt = 1; attempt <= MAX_RECONNECT_ATTEMPTS; attempt++) {
            Log.i(TAG, "重连尝试 / reconnect attempt " + attempt + " → " + target);
            if (connect(target)) {
                return true;
            }
            sleepQuietly(400L * attempt);
        }
        return false;
    }

    /** 建立 socket 并取输出流 / open the socket and grab the output stream. */
    private boolean openAndBind(BluetoothDevice device, boolean secure) {
        BluetoothSocket candidate = null;
        try {
            candidate = secure
                    ? device.createRfcommSocketToServiceRecord(SPP_UUID)
                    : device.createInsecureRfcommSocketToServiceRecord(SPP_UUID);
        } catch (IOException e) {
            setError("创建蓝牙 socket 失败 / cannot create Bluetooth socket: " + e.getMessage());
            return false;
        } catch (SecurityException e) {
            setError("缺少蓝牙权限(BLUETOOTH_CONNECT)/ missing BLUETOOTH_CONNECT permission");
            return false;
        }

        // BluetoothSocket.connect() 没有超时参数,而且可能卡十几秒。这里放到
        // 独立线程里跑,超时就 close() —— close 会把阻塞中的 connect 顶出来。
        // BluetoothSocket.connect() takes no timeout and can hang for a dozen
        // seconds, so it runs on its own thread; on timeout we close() the
        // socket, which unblocks the pending connect.
        final BluetoothSocket sock = candidate;
        final AtomicBoolean finished = new AtomicBoolean(false);
        Thread connector = new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    sock.connect();
                } catch (Exception e) {
                    setError("连接失败 / connect failed: " + e.getMessage());
                    Log.w(TAG, "connect 异常 / exception: " + e.getMessage());
                } finally {
                    finished.set(true);
                }
            }
        }, "printtheshot-bt-connect");
        connector.setDaemon(true);
        connector.start();
        try {
            connector.join(CONNECT_TIMEOUT_MS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }

        if (!finished.get()) {
            closeQuietly(candidate);
            setError("连接超时:打印机没响应 / connect timed out, no response from the printer");
            return false;
        }

        try {
            if (!candidate.isConnected()) {
                closeQuietly(candidate);
                if (lastError == null || lastError.isEmpty()) {
                    setError("连接未建立 / connection was not established");
                }
                return false;
            }
            this.socket = candidate;
            this.outputStream = candidate.getOutputStream();
            return true;
        } catch (IOException e) {
            closeQuietly(candidate);
            setError("取输出流失败 / cannot open the output stream: " + e.getMessage());
            return false;
        }
    }

    /**
     * 断开连接 / drop the connection.
     *
     * 幂等,没连时调用是安全的 / idempotent and safe to call when idle.
     */
    public void disconnect() {
        connected = false;
        connectedAddress = null;
        outputStream = null;
        BluetoothSocket sock = socket;
        socket = null;
        closeQuietly(sock);
    }

    /**
     * 写入一份完整的 ESC/POS 字节流 / write one complete ESC/POS byte stream.
     *
     * 数据是上层(Web / Python)已经拼好的整份任务,**原样写入,不做任何解释或
     * 补拼** —— 这一点是刻意设计的:字节怎么组装只由 Web 侧一处决定,原生层
     * 只是个搬运工。
     * The payload is a complete job assembled upstream (web / Python) and is
     * written **verbatim**: how those bytes are composed is decided in exactly
     * one place (the web side), and the native layer is only a pipe.
     *
     * 必须在工作线程调用 / must be called on a worker thread.
     *
     * @param data ESC/POS 完整字节流 / the complete ESC/POS byte stream
     */
    public boolean write(byte[] data) {
        if (data == null || data.length == 0) {
            setError("打印数据为空 / empty print payload");
            return false;
        }
        if (!ensureConnected()) {
            return false;
        }

        OutputStream out = outputStream;
        if (out == null) {
            setError("连接不可用 / connection is not usable");
            return false;
        }

        int offset = 0;
        int attempt = 0;
        while (offset < data.length) {
            int length = Math.min(CHUNK_SIZE, data.length - offset);
            try {
                out.write(data, offset, length);
                out.flush();
                offset += length;
            } catch (IOException e) {
                Log.w(TAG, "写入失败 / write failed at offset " + offset + ": " + e.getMessage());
                disconnect();
                // 一个字节都还没发出去 → 重连后重发整份任务;已经发了一半就
                // 老老实实报错,绝不重打(否则小票会重复半张)。
                // Nothing has been written yet, so reconnecting and resending
                // the whole job is safe; once part of it is out, fail honestly
                // instead of duplicating half a receipt.
                if (offset == 0 && attempt < MAX_RECONNECT_ATTEMPTS) {
                    attempt++;
                    if (!ensureConnected()) {
                        return false;
                    }
                    out = outputStream;
                    if (out == null) {
                        return false;
                    }
                    continue;
                }
                setError("打印中断(第 " + offset + " 字节处)/ print interrupted at byte "
                        + offset + ": " + e.getMessage());
                return false;
            }
            if (offset < data.length) {
                sleepQuietly(CHUNK_GAP_MS);
            }
        }

        clearError();
        Log.i(TAG, "已发送 / sent " + data.length + " bytes");
        return true;
    }

    /**
     * 打印一份完整任务,必要时先连到指定打印机 /
     * print one complete job, connecting to the given printer first if needed.
     *
     * 「该用哪台打印机」的判断只写在这一处:插件(JS)、静态桥(pyjnius)和
     * HTTP 桥三个入口都转调这里,省得三份实现各写一遍、各有各的 bug。
     * The "which printer" decision lives here and only here, so the plugin
     * (JS), the static bridge (pyjnius) and the HTTP bridge all share it
     * instead of drifting apart in three copies.
     *
     * 必须在工作线程调用 / must be called on a worker thread.
     *
     * @param payload 完整 ESC/POS 字节流,原样写出 / complete ESC/POS byte stream,
     *                written verbatim
     * @param address 目标地址;null 或空串表示「用当前连接或上次记住的那台」/
     *                target address; null or empty means "reuse the current
     *                connection or the last remembered printer"
     */
    public boolean printJob(byte[] payload, String address) {
        if (address != null && !address.trim().isEmpty()) {
            String target = address.trim();
            if (!target.equalsIgnoreCase(getConnectedAddress())) {
                if (!connect(target)) {
                    return false;
                }
            }
        }
        return write(payload);
    }

    /**
     * 连接状态,可以从主线程读 / connection state, safe from the main thread.
     *
     * 是「尽力而为」的判断:socket 存在且未报错就算连着。打印机半路掉电不一
     * 定能立刻看出来,真正的确认发生在下一次写数据时。
     * Best effort: a live socket with no recorded failure counts as connected.
     * A printer losing power mid-session may not be noticed until the next
     * write, which is where real confirmation happens.
     */
    public boolean isConnected() {
        BluetoothSocket sock = socket;
        return connected && sock != null && sock.isConnected();
    }

    /** 当前连接的地址,未连接返回 null / current address, null when idle. */
    public String getConnectedAddress() {
        return isConnected() ? connectedAddress : null;
    }

    /** 最近一次错误描述 / description of the most recent error. */
    public String getLastError() {
        return lastError == null ? "" : lastError;
    }

    // ------------------------------------------------------------------
    // 默认打印机 / default printer
    // ------------------------------------------------------------------

    /**
     * 上次连上的打印机地址 / address of the last printer connected to.
     *
     * 只存在本机 SharedPreferences 里,不上传 / kept in local
     * SharedPreferences only, never uploaded anywhere.
     */
    public String getDefaultAddress() {
        if (appContext == null) {
            return null;
        }
        SharedPreferences prefs = appContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        String value = prefs.getString(KEY_DEFAULT_ADDRESS, null);
        return value == null || value.isEmpty() ? null : value;
    }

    /** 记住默认打印机 / remember the default printer. */
    public void setDefaultAddress(String address) {
        if (appContext == null || address == null || address.isEmpty()) {
            return;
        }
        appContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                .edit()
                .putString(KEY_DEFAULT_ADDRESS, address)
                .apply();
    }

    /** 清掉默认打印机 / forget the default printer. */
    public void clearDefaultAddress() {
        if (appContext == null) {
            return;
        }
        appContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                .edit()
                .remove(KEY_DEFAULT_ADDRESS)
                .apply();
    }

    // ------------------------------------------------------------------
    // 内部工具 / internals
    // ------------------------------------------------------------------

    /**
     * 取蓝牙适配器 / obtain the Bluetooth adapter.
     *
     * 优先用 BluetoothManager;个别机型上它返回 null,再退回已被标记过时的
     * getDefaultAdapter() —— 后者虽然过时,但在所有 API 版本上都还能用。
     * BluetoothManager first; some units return null there, so fall back to
     * the deprecated getDefaultAdapter(), which still works on every API level.
     */
    @SuppressLint("MissingPermission")
    private BluetoothAdapter getAdapter() {
        if (appContext != null) {
            try {
                BluetoothManager manager =
                        (BluetoothManager) appContext.getSystemService(Context.BLUETOOTH_SERVICE);
                if (manager != null && manager.getAdapter() != null) {
                    return manager.getAdapter();
                }
            } catch (Exception e) {
                Log.w(TAG, "取 BluetoothManager 失败 / BluetoothManager unavailable: " + e.getMessage());
            }
        }
        try {
            return BluetoothAdapter.getDefaultAdapter();
        } catch (Exception e) {
            return null;
        }
    }

    @SuppressLint("MissingPermission")
    private String safeAddress(BluetoothDevice device) {
        try {
            return device.getAddress();
        } catch (SecurityException e) {
            return null;
        }
    }

    @SuppressLint("MissingPermission")
    private String safeName(BluetoothDevice device) {
        try {
            String name = device.getName();
            return name == null || name.trim().isEmpty() ? null : name.trim();
        } catch (SecurityException e) {
            return null;
        }
    }

    private void closeQuietly(BluetoothSocket sock) {
        if (sock == null) {
            return;
        }
        try {
            sock.close();
        } catch (IOException e) {
            Log.w(TAG, "关闭 socket 失败 / close failed: " + e.getMessage());
        }
    }

    private void sleepQuietly(long millis) {
        try {
            Thread.sleep(millis);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    private void setError(String message) {
        this.lastError = message == null ? "" : message;
        Log.w(TAG, this.lastError);
    }

    private void clearError() {
        this.lastError = "";
    }

    /** 打印机条目 / one printer entry. */
    public static final class PrinterEntry {

        /** 蓝牙 MAC 地址,例如 AA:BB:CC:DD:EE:FF / Bluetooth MAC address. */
        public final String address;

        /** 设备名,取不到时为 null / device name, null when unreadable. */
        public final String name;

        /** 是否已配对(已绑定)/ whether the device is bonded (paired). */
        public final boolean paired;

        /** 是否是上次成功连接的那台 / whether it was the last one connected. */
        public final boolean isDefault;

        PrinterEntry(String address, String name, boolean paired, boolean isDefault) {
            this.address = address;
            this.name = name;
            this.paired = paired;
            this.isDefault = isDefault;
        }

        /** 显示名:没名字就退回地址 / display name, falling back to the address. */
        public String displayName() {
            return name != null ? name : address;
        }

        @Override
        public String toString() {
            return displayName() + " (" + address + ")";
        }
    }
}
