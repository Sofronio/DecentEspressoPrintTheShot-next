package com.printtheshot.printer;

import android.content.Context;
import android.util.Base64;
import android.util.Log;

import java.util.List;
import java.util.concurrent.Callable;

/**
 * 静态蓝牙打印入口(给 pyjnius / 非 Capacitor 调用方)/
 * Static Bluetooth printing entry point (for pyjnius / non-Capacitor callers)
 * ==============================================================================
 *
 * 中文
 * ----
 * Capacitor 里的 JS 走 {@link PrintTheShotPrinterPlugin},而这个类是给**进程内
 * 的非 JS 调用方**用的,典型场景是 Python 跑在同一个 App 里(python-for-android /
 * Chaquopy 之类)通过 pyjnius 直接调 Java 静态方法 —— 几条路径共用同一个
 * {@link BluetoothPrinter} 单例,也就是同一条蓝牙连接。
 *
 * 用法 / usage:
 * <pre>
 *   // 1) 先注入一次上下文(App 启动时调一次即可,插件与前台服务也会自动调)
 *   BluetoothPrinterBridge.attach(context);
 *
 *   // 2) 之后随时打印,data 是 base64 编码的完整 ESC/POS 字节流
 *   boolean ok = BluetoothPrinterBridge.printRaw(base64Data, "AA:BB:CC:DD:EE:FF");
 *   if (!ok) { String why = BluetoothPrinterBridge.getLastError(); }
 * </pre>
 *
 * 关于阻塞 / on blocking:
 *   {@link #printRaw(String, String)} 是**同步阻塞**的 —— 调用方拿得到布尔返回
 *   值,但必须自己保证不在 UI 线程上调用(在 Python 里就是别在主线程调)。内部
 *   把活派给蓝牙工作队列,再等结果,超时返回 false。
 *   {@link #printRaw(String, String)} blocks until the job is done: that is
 *   what makes a boolean return value possible, but the caller must stay off
 *   the UI thread (in Python: off the main thread). The work itself is handed
 *   to the Bluetooth worker queue and the call waits for the result, returning
 *   false on timeout.
 *
 * English
 * -------
 * JS inside Capacitor goes through {@link PrintTheShotPrinterPlugin}; this
 * class exists for **non-JS callers in the same process**, typically Python
 * embedded in the app (python-for-android / Chaquopy and friends) calling a
 * static Java method through pyjnius. Every path shares the same
 * {@link BluetoothPrinter} singleton, i.e. the same Bluetooth link.
 */
public final class BluetoothPrinterBridge {

    private static final String TAG = BluetoothPrinter.TAG;

    /** 连接/枚举这类小操作的超时(毫秒)/ timeout for small operations, ms. */
    private static final long SHORT_OP_TIMEOUT_MS = 30000L;

    private BluetoothPrinterBridge() {
    }

    /**
     * 注入应用上下文 / inject the application context.
     *
     * 幂等,可以重复调用。App 启动时调一次最稳:Capacitor 的 MainActivity、
     * 插件 load()、前台服务 onCreate() 都会调,所以正常情况不用手动管。
     * Idempotent. Calling it once at startup is the safest; MainActivity, the
     * plugin's load() and the foreground service's onCreate() all do, so in
     * practice it needs no manual attention.
     */
    public static void attach(Context context) {
        BluetoothPrinter.get().init(context);
    }

    /** 上下文是否就绪 / whether a context is available. */
    public static boolean isReady() {
        return BluetoothPrinter.get().isReady();
    }

    /**
     * 打印一份 base64 编码的 ESC/POS 字节流 / print a base64-encoded ESC/POS
     * byte stream.
     *
     * data 里已经是完整任务(复位 → 光栅位图 → 走纸 → 切纸),这里**原样写进
     * 蓝牙 socket**,不解析、不补拼、不改一个字节。
     * The payload is already a complete job (reset → raster bitmap → feed →
     * cut) and is written **verbatim** into the Bluetooth socket — no parsing,
     * no extra commands, not one byte changed.
     *
     * @param base64Data base64 编码的 ESC/POS 字节流 /
     *                   base64-encoded ESC/POS byte stream
     * @param address    目标打印机 MAC;传 null/空串则用当前连接或上次记住的那台 /
     *                   target printer MAC; null or empty reuses the current
     *                   connection or the last remembered printer
     * @return 成功 true,失败 false(原因见 {@link #getLastError()})/
     *         true on success, false on failure (see {@link #getLastError()})
     */
    public static boolean printRaw(String base64Data, String address) {
        if (base64Data == null || base64Data.isEmpty()) {
            Log.w(TAG, "printRaw: 数据为空 / empty payload");
            return false;
        }

        final byte[] payload;
        try {
            payload = Base64.decode(base64Data, Base64.DEFAULT);
        } catch (IllegalArgumentException e) {
            Log.w(TAG, "printRaw: base64 解码失败 / base64 decode failed: " + e.getMessage());
            return false;
        }
        if (payload.length == 0) {
            Log.w(TAG, "printRaw: 解码后为空 / payload empty after decoding");
            return false;
        }

        if (!isReady()) {
            Log.w(TAG, "printRaw: 上下文未注入,请先调用 attach() / "
                    + "no context injected, call attach() first");
            return false;
        }

        final String target = address == null ? "" : address.trim();
        long timeout = BluetoothPrinter.timeoutForPayload(payload.length);

        try {
            Boolean ok = BluetoothPrinter.get().submitAndWait(new Callable<Boolean>() {
                @Override
                public Boolean call() {
                    return Boolean.valueOf(BluetoothPrinter.get().printJob(payload, target));
                }
            }, timeout);
            return ok != null && ok.booleanValue();
        } catch (Exception e) {
            // 超时 / 中断 / 工作线程内抛出 / timeout, interruption, or a throw
            // from the worker thread
            Log.w(TAG, "printRaw 失败 / printRaw failed: " + e);
            return false;
        }
    }

    /**
     * 连接指定打印机 / connect to a printer.
     *
     * 同步阻塞,同 {@link #printRaw} / blocking, same as {@link #printRaw}.
     */
    public static boolean connect(String address) {
        if (!isReady() || address == null || address.trim().isEmpty()) {
            return false;
        }
        final String target = address.trim();
        try {
            Boolean ok = BluetoothPrinter.get().submitAndWait(new Callable<Boolean>() {
                @Override
                public Boolean call() {
                    return Boolean.valueOf(BluetoothPrinter.get().connect(target));
                }
            }, SHORT_OP_TIMEOUT_MS);
            return ok != null && ok.booleanValue();
        } catch (Exception e) {
            Log.w(TAG, "connect 失败 / connect failed: " + e);
            return false;
        }
    }

    /**
     * 断开连接 / drop the connection.
     *
     * 同步阻塞但很快 / blocking, but quick.
     */
    public static void disconnect() {
        try {
            BluetoothPrinter.get().submitAndWait(new Callable<Boolean>() {
                @Override
                public Boolean call() {
                    BluetoothPrinter.get().disconnect();
                    return Boolean.TRUE;
                }
            }, 5000L);
        } catch (Exception e) {
            Log.w(TAG, "disconnect 失败 / disconnect failed: " + e);
        }
    }

    /** 是否已连接(可从任意线程调)/ whether a printer is connected (any thread). */
    public static boolean isConnected() {
        return BluetoothPrinter.get().isConnected();
    }

    /** 当前连接地址,未连接返回 null / current address, null when idle. */
    public static String getConnectedAddress() {
        return BluetoothPrinter.get().getConnectedAddress();
    }

    /**
     * 最近一次错误描述 / description of the most recent error.
     *
     * 给调用方拼日志用,失败时它基本就是原因 / for the caller's log line; on
     * failure this is usually the reason.
     */
    public static String getLastError() {
        return BluetoothPrinter.get().getLastError();
    }

    /**
     * 已配对打印机列表,每条形如 `AA:BB:CC:DD:EE:FF|HP-58B` /
     * bonded printers, one `AA:BB:CC:DD:EE:FF|HP-58B` per entry.
     *
     * Python 侧如果需要结构化数据,推荐走 HTTP 桥(见 {@link LocalPrintBridge}),
     * 那边返回的是 JSON。这里给的是最省事的一行一条,方便直接打印到日志里。
     * For structured data on the Python side prefer the HTTP bridge
     * ({@link LocalPrintBridge}), which returns JSON; this one-line-per-printer
     * form is meant for quick logging.
     *
     * 同步阻塞 / blocking.
     */
    public static String listPrinters() {
        if (!isReady()) {
            return "";
        }
        try {
            String text = BluetoothPrinter.get().submitAndWait(new Callable<String>() {
                @Override
                public String call() {
                    List<BluetoothPrinter.PrinterEntry> entries = BluetoothPrinter.get().listPrinters();
                    StringBuilder sb = new StringBuilder();
                    for (BluetoothPrinter.PrinterEntry entry : entries) {
                        if (sb.length() > 0) {
                            sb.append('\n');
                        }
                        sb.append(entry.address).append('|').append(entry.displayName());
                    }
                    return sb.toString();
                }
            }, 10000L);
            return text == null ? "" : text;
        } catch (Exception e) {
            Log.w(TAG, "listPrinters 失败 / listPrinters failed: " + e);
            return "";
        }
    }
}
