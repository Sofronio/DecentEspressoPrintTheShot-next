package com.printtheshot.printer;

import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.util.Base64;
import android.util.Log;

import androidx.core.app.ActivityCompat;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Capacitor 蓝牙打印插件 / Capacitor Bluetooth printing plugin
 * ==============================================================================
 *
 * 中文
 * ----
 * Web 端(web/printer.js)通过 `Capacitor.Plugins.PrintTheShotPrinter` 调这里。
 * 方法名是**接口约定**,不能改:
 *
 * <pre>
 *   await PrintTheShotPrinter.requestPermissions();            // {granted, notifications}
 *   const { printers } = await PrintTheShotPrinter.listPrinters();
 *   await PrintTheShotPrinter.connect({ address });            // {success, message}
 *   await PrintTheShotPrinter.printRaw({ data, address });     // {success, message}
 *   await PrintTheShotPrinter.isConnected();                   // {connected, address}
 *   await PrintTheShotPrinter.disconnect();                    // {success}
 *   await PrintTheShotPrinter.startForegroundService();        // {success, running}
 *   await PrintTheShotPrinter.stopForegroundService();         // {success, running}
 *   await PrintTheShotPrinter.savePlugin({ name });            // {success, path, message}
 *   await PrintTheShotPrinter.exportBackup();                  // {success, path, bytes, message}
 *   await PrintTheShotPrinter.importBackup();                  // {success, imported, message}
 * </pre>
 *
 * 关于 printRaw 的 data / on printRaw's data:
 *   收到的是**完整**的 ESC/POS 字节流(base64):复位 → GS v 0 光栅位图 →
 *   走纸 → 切纸,全都已经由 Web 侧拼好。原生层只负责原样写进蓝牙 socket,
 *   不解析、不补指令、不改一个字节 —— 字节怎么拼只由 Web 侧一处决定。
 *   The payload is the **complete** ESC/POS byte stream (base64): reset →
 *   `GS v 0` raster bitmap → feed → cut, all assembled by the web side. The
 *   native layer only writes it verbatim into the Bluetooth socket — no
 *   parsing, no extra commands, not a byte changed. How the bytes are composed
 *   is decided in exactly one place: the web side.
 *
 * 关于错误 / on errors:
 *   参数本身有问题(比如 data 不是合法 base64)会 reject,JS 侧会进 catch;
 *   而**打印失败**(没连接、打印机没开、写到一半断了)是 resolve 成
 *   `{success: false, message: "..."}`,因为「打印没成功」是一个正常结果,
 *   不是调用错误。JS 侧请判断返回值里的 success。
 *   Malformed arguments (e.g. data that is not valid base64) reject, so the JS
 *   side lands in catch. An actual **print failure** (not connected, printer
 *   off, link dropped mid-job) resolves as `{success: false, message: "..."}`:
 *   "the print did not happen" is a legitimate outcome, not a call error. Check
 *   `success` in the resolved value.
 *
 * English
 * -------
 * The web side (web/printer.js) reaches this class through
 * `Capacitor.Plugins.PrintTheShotPrinter`. The method names below are the
 * interface contract and must not change.
 */
@CapacitorPlugin(
        name = "PrintTheShotPrinter",
        // 必须在这里登记,否则 Bridge.onActivityResult 找不到该把文件选择的结果派给
        // 谁:它按 requestCodes 反查插件,查不到就转给 Cordova 那条路,结果永远回不来。
        // 注解值必须是编译期常量,所以下面那个字段也得是 static final。
        //
        // This registration is required: Bridge.onActivityResult looks the plugin up by
        // request code and, finding nothing, falls through to the Cordova path — so the
        // picked file would never come back. Annotation values must be compile-time
        // constants, hence the static final field below.
        requestCodes = {PrintTheShotPrinterPlugin.REQ_IMPORT_BACKUP},
        permissions = {
                // 这里刻意用字符串字面量而不是 Manifest.permission.* 常量:
                // BLUETOOTH_CONNECT / POST_NOTIFICATIONS 要 compileSdk 31/33 才存在,
                // 写字面量就不用管编译 SDK 版本。注解值必须是编译期常量,所以不能
                // 引用下面那些 private static final 字段。
                // String literals are used on purpose instead of Manifest.permission.*
                // constants: BLUETOOTH_CONNECT and POST_NOTIFICATIONS only exist from
                // compileSdk 31/33, and literals make the compile SDK version
                // irrelevant. Annotation values must be compile-time constants, hence
                // the duplication with the fields below.
                @Permission(alias = PrintTheShotPrinterPlugin.ALIAS_BLUETOOTH_CONNECT,
                        strings = {"android.permission.BLUETOOTH_CONNECT"}),
                @Permission(alias = PrintTheShotPrinterPlugin.ALIAS_BLUETOOTH_SCAN,
                        strings = {"android.permission.BLUETOOTH_SCAN"}),
                @Permission(alias = PrintTheShotPrinterPlugin.ALIAS_NOTIFICATIONS,
                        strings = {"android.permission.POST_NOTIFICATIONS"})
        }
)
public class PrintTheShotPrinterPlugin extends Plugin {

    /** 日志标签 / log tag. */
    private static final String TAG = BluetoothPrinter.TAG;

    /** 选备份文件的 request code / request code for picking a backup file. */
    static final int REQ_IMPORT_BACKUP = 0x9B01;

    /** 平板自己的服务端 / this tablet's own server. */
    private static final String SELF_BASE = "http://localhost:8000";

    /** 权限别名:蓝牙连接 / permission alias: Bluetooth connect. */
    static final String ALIAS_BLUETOOTH_CONNECT = "bluetoothConnect";

    /** 权限别名:蓝牙扫描 / permission alias: Bluetooth scan. */
    static final String ALIAS_BLUETOOTH_SCAN = "bluetoothScan";

    /** 权限别名:通知(前台服务保活要用)/ permission alias: notifications. */
    static final String ALIAS_NOTIFICATIONS = "notifications";

    /** Android 12 (API 31) 起蓝牙权限改为运行时申请 / runtime BT permissions from API 31. */
    private static final String PERM_BLUETOOTH_CONNECT = "android.permission.BLUETOOTH_CONNECT";
    private static final String PERM_BLUETOOTH_SCAN = "android.permission.BLUETOOTH_SCAN";

    /** Android 13 (API 33) 起发通知要用户同意 / notification consent from API 33. */
    private static final String PERM_POST_NOTIFICATIONS = "android.permission.POST_NOTIFICATIONS";

    /** 权限回调的兜底等待时间(毫秒)/ safety-net wait for the permission callback. */
    private static final long PERMISSION_TIMEOUT_MS = 30000L;

    /** 权限回调方法名,必须与下面 @PermissionCallback 的方法同名 /
     *  callback name; must match the @PermissionCallback method below. */
    private static final String PERMISSION_CALLBACK = "permissionsCallback";

    private final Handler mainHandler = new Handler(Looper.getMainLooper());

    /** 保证一次权限请求只 resolve 一次 / makes sure one request resolves exactly once. */
    private final AtomicBoolean permissionSettled = new AtomicBoolean(true);

    /** 兜底:权限对话框一直没结果时把 JS 的 await 放掉 / unblocks JS if the dialog never returns. */
    private final Runnable permissionTimeout = new Runnable() {
        @Override
        public void run() {
            Log.w(TAG, "权限回调超时,按未授权返回 / permission callback timed out, reporting not granted");
            settlePermission(false);
        }
    };

    private PluginCall pendingPermissionCall;

    // ------------------------------------------------------------------
    // 生命周期 / lifecycle
    // ------------------------------------------------------------------

    @Override
    public void load() {
        // 上下文给蓝牙单例,顺便让 pyjnius / HTTP 桥也能直接用 /
        // hand the context to the Bluetooth singleton so the pyjnius and HTTP
        // bridges work too
        BluetoothPrinter.get().init(getContext());
        BluetoothPrinterBridge.attach(getContext());
    }

    // ------------------------------------------------------------------
    // 权限 / permissions
    // ------------------------------------------------------------------

    /**
     * 申请打印需要的运行时权限 / request the runtime permissions printing needs.
     *
     * Android 12 (API 31) 起 BLUETOOTH_CONNECT / BLUETOOTH_SCAN 变成运行时权限;
     * Android 11 及以下它们是安装时就给的,所以老系统上直接返回 granted。
     * Android 13 (API 33) 起发通知也要用户同意,前台服务的保活通知需要它 ——
     * 但它只影响保活,不影响打印,所以单独放在 notifications 字段里返回。
     * Since Android 12 (API 31) BLUETOOTH_CONNECT / BLUETOOTH_SCAN are runtime
     * permissions; on Android 11 and below they are granted at install time, so
     * older devices get `granted: true` straight away. Android 13 (API 33) also
     * requires consent for notifications, which the foreground service's
     * keep-alive notification needs — but that only affects keep-alive, never
     * printing, so it is reported separately as `notifications`.
     *
     * @return `{granted: boolean, notifications: boolean}`
     */
    @PluginMethod
    public void requestPermissions(PluginCall call) {
        List<String> missing = missingBluetoothAliases();
        boolean notificationsMissing = isNotificationConsentMissing();

        if (missing.isEmpty() && !notificationsMissing) {
            // 都齐了 / nothing left to ask for
            JSObject ret = new JSObject();
            ret.put("granted", true);
            ret.put("notifications", true);
            call.resolve(ret);
            return;
        }

        List<String> aliases = new ArrayList<>(missing);
        if (notificationsMissing) {
            aliases.add(ALIAS_NOTIFICATIONS);
        }

        // requestPermissionForAliases 在「没有任何需要弹窗的权限」时会静默返回、
        // 既不回调也不 resolve,那样 JS 的 await 会永远挂着。上面的判断保证了走到
        // 这里时至少有一个别名是没授权的,再加上超时兜底,双重保险。
        // requestPermissionForAliases returns silently — no callback, no resolve —
        // when nothing needs prompting, which would leave the JS await hanging
        // forever. The check above guarantees at least one alias is missing here,
        // and the timeout below is a second line of defence.
        permissionSettled.set(false);
        pendingPermissionCall = call;
        mainHandler.removeCallbacks(permissionTimeout);
        mainHandler.postDelayed(permissionTimeout, PERMISSION_TIMEOUT_MS);
        requestPermissionForAliases(aliases.toArray(new String[0]), call, PERMISSION_CALLBACK);
    }

    /** Capacitor 权限回调 / Capacitor permission callback. */
    @PermissionCallback
    private void permissionsCallback(PluginCall call) {
        mainHandler.removeCallbacks(permissionTimeout);
        boolean bluetoothGranted = missingBluetoothAliases().isEmpty();
        boolean notificationsGranted = !isNotificationConsentMissing();

        JSObject ret = new JSObject();
        ret.put("granted", bluetoothGranted);
        ret.put("notifications", notificationsGranted);
        if (!permissionSettled.compareAndSet(false, true)) {
            return;
        }
        pendingPermissionCall = null;
        call.resolve(ret);
    }

    /** 兜底路径用:直接给个结果 / used by the safety net: settle with a plain result. */
    private void settlePermission(boolean granted) {
        if (!permissionSettled.compareAndSet(false, true)) {
            return;
        }
        PluginCall call = pendingPermissionCall;
        pendingPermissionCall = null;
        if (call == null) {
            return;
        }
        JSObject ret = new JSObject();
        ret.put("granted", granted);
        ret.put("notifications", !isNotificationConsentMissing());
        call.resolve(ret);
    }

    /** 还没授权的蓝牙权限别名 / Bluetooth aliases that are not granted yet. */
    private List<String> missingBluetoothAliases() {
        List<String> missing = new ArrayList<>();
        // Android 11 及以下:BLUETOOTH / BLUETOOTH_ADMIN 是安装时权限,不用问
        // Android 11 and below: BLUETOOTH / BLUETOOTH_ADMIN come at install time
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) {
            return missing;
        }
        if (!isGranted(PERM_BLUETOOTH_CONNECT)) {
            missing.add(ALIAS_BLUETOOTH_CONNECT);
        }
        if (!isGranted(PERM_BLUETOOTH_SCAN)) {
            missing.add(ALIAS_BLUETOOTH_SCAN);
        }
        return missing;
    }

    /** 通知权限是否还没给 / whether notification consent is still missing. */
    private boolean isNotificationConsentMissing() {
        return Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU && !isGranted(PERM_POST_NOTIFICATIONS);
    }

    /**
     * 权限是否已授予 / whether a permission is granted.
     *
     * 用 ContextCompat 的封装而不是 Context.checkSelfPermission,因为后者是
     * API 23 才有的方法,而这个 App 的 minSdk 更低。
     * Uses the androidx wrapper rather than Context.checkSelfPermission, which
     * only exists from API 23 while this app supports lower minSdk levels.
     */
    private boolean isGranted(String permission) {
        Context context = getContext();
        return context != null
                && ActivityCompat.checkSelfPermission(context, permission) == android.content.pm.PackageManager.PERMISSION_GRANTED;
    }

    // ------------------------------------------------------------------
    // 打印机枚举 / printer enumeration
    // ------------------------------------------------------------------

    /**
     * 已配对的打印机列表 / list bonded printers.
     *
     * @return `{printers: [{address, name, paired, default}]}`
     */
    @PluginMethod
    public void listPrinters(PluginCall call) {
        final BluetoothPrinter printer = BluetoothPrinter.get();
        printer.executor().execute(new Runnable() {
            @Override
            public void run() {
                List<BluetoothPrinter.PrinterEntry> entries = printer.listPrinters();
                try {
                    JSONArray array = new JSONArray();
                    for (BluetoothPrinter.PrinterEntry entry : entries) {
                        JSONObject item = new JSONObject();
                        item.put("address", entry.address);
                        item.put("name", entry.displayName());
                        item.put("paired", entry.paired);
                        item.put("default", entry.isDefault);
                        array.put(item);
                    }
                    JSONObject root = new JSONObject();
                    root.put("printers", array);
                    call.resolve(new JSObject(root.toString()));
                } catch (JSONException e) {
                    Log.w(TAG, "打印机列表序列化失败 / cannot serialise the printer list: " + e.getMessage());
                    call.reject("打印机列表序列化失败 / cannot serialise the printer list");
                }
            }
        });
    }

    // ------------------------------------------------------------------
    // 连接 / connection
    // ------------------------------------------------------------------

    /**
     * 连接打印机 / connect to a printer.
     *
     * @return `{success: boolean, message: String}`
     */
    @PluginMethod
    public void connect(final PluginCall call) {
        final String address = call.getString("address");
        if (address == null || address.trim().isEmpty()) {
            call.reject("connect 需要 address 参数 / connect needs an 'address' argument");
            return;
        }
        final BluetoothPrinter printer = BluetoothPrinter.get();
        printer.executor().execute(new Runnable() {
            @Override
            public void run() {
                boolean ok = printer.connect(address.trim());
                JSObject ret = new JSObject();
                ret.put("success", ok);
                ret.put("message", ok
                        ? "已连接 / connected: " + address.trim()
                        : printer.getLastError());
                call.resolve(ret);
            }
        });
    }

    /**
     * 断开连接 / drop the connection.
     *
     * @return `{success: boolean}`
     */
    @PluginMethod
    public void disconnect(PluginCall call) {
        final BluetoothPrinter printer = BluetoothPrinter.get();
        printer.executor().execute(new Runnable() {
            @Override
            public void run() {
                printer.disconnect();
                JSObject ret = new JSObject();
                ret.put("success", true);
                call.resolve(ret);
            }
        });
    }

    /**
     * 当前连接状态 / current connection state.
     *
     * @return `{connected: boolean, address: String}`(未连接时 address 是空串)/
     *         (address is an empty string when idle)
     */
    @PluginMethod
    public void isConnected(PluginCall call) {
        BluetoothPrinter printer = BluetoothPrinter.get();
        String address = printer.getConnectedAddress();
        JSObject ret = new JSObject();
        ret.put("connected", printer.isConnected());
        ret.put("address", address == null ? "" : address);
        call.resolve(ret);
    }

    // ------------------------------------------------------------------
    // 插件文件导出 / exporting the plugin file
    // ------------------------------------------------------------------

    /**
     * 把打进 APK 的 DE1 插件写到设备的「下载」目录 /
     * write the bundled DE1 plugin into the device's Downloads folder.
     *
     * 为什么不走「开浏览器下载」那条路
     * --------------------------------
     * 那是我先试过的方案,它自相矛盾:浏览器一起来,本 App 就退到后台,而下载要从
     * **本 App 自己的服务端**取 —— 提供文件的那一端在被取的那一刻被系统冻住,于是
     * 浏览器打开一个空白页,什么也没存下。
     *
     * 而且根本没必要联网:plugin.tcl 本来就在 APK 里(assets/public/plugin/)。
     * 直接写出去,不依赖服务端、不依赖 App 在不在前台。
     *
     * Why not "open a browser and download"
     * ------------------------------------
     * That was the first attempt, and it is self-defeating: opening the browser puts this
     * app in the background, while the file has to come from **this app's own server** —
     * so the side providing the file gets frozen at the moment it is asked, the browser
     * opens a blank page, and nothing is saved.
     *
     * Networking is not needed anyway: plugin.tcl already ships inside the APK
     * (assets/public/plugin/). Writing it out needs no server and no foreground app.
     *
     * @return `{success: boolean, path: string, message: string}`
     */
    @PluginMethod
    public void savePlugin(PluginCall call) {
        String name = call.getString("name", "plugin.tcl");
        // 只放行这两个名字 —— 它们对应界面上的两个按钮
        // Only these two names: they are what the two buttons ask for
        boolean isTxt = "plugin.tcl.txt".equals(name);
        if (!isTxt && !"plugin.tcl".equals(name)) {
            call.reject("unknown plugin file: " + name);
            return;
        }
        // TXT 版存下来的名字就是 tcl.txt(它存在的理由就是蓝牙发送时安卓端拒收 .tcl)
        // The TXT copy saves as tcl.txt — it exists because Android refuses .tcl over Bluetooth
        String outName = isTxt ? "tcl.txt" : "plugin.tcl";

        try (java.io.InputStream in = getContext().getAssets().open("public/plugin/" + name)) {
            java.io.ByteArrayOutputStream buf = new java.io.ByteArrayOutputStream();
            byte[] chunk = new byte[8192];
            int n;
            while ((n = in.read(chunk)) > 0) buf.write(chunk, 0, n);
            byte[] data = buf.toByteArray();

            String path = writeToDownloads(outName, data, "text/plain");

            JSObject ret = new JSObject();
            ret.put("success", true);
            ret.put("path", path);
            ret.put("message", "已保存到「下载」/ saved to Downloads: " + outName);
            call.resolve(ret);
        } catch (Exception e) {
            Log.w(TAG, "导出插件失败 / cannot export the plugin: " + e.getMessage());
            call.reject("导出失败 / export failed: " + e.getMessage());
        }
    }

    /**
     * 写进公共下载目录 / write into the public Downloads folder.
     *
     * Android 10 (API 29) 起是分区存储,必须通过 MediaStore 落盘 —— 直接写
     * Environment.getExternalStoragePublicDirectory() 那条路在新系统上会失败。
     * 更早的系统上没有 MediaStore.Downloads,退回直接写,并需要 WRITE_EXTERNAL_STORAGE。
     *
     * From Android 10 (API 29) scoped storage applies and the file has to go through
     * MediaStore; writing to Environment.getExternalStoragePublicDirectory() directly
     * fails on newer systems. Older ones have no MediaStore.Downloads, so they fall back
     * to a plain write, which needs WRITE_EXTERNAL_STORAGE.
     */
    private String writeToDownloads(String outName, byte[] data, String mime) throws Exception {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            android.content.ContentValues values = new android.content.ContentValues();
            values.put(android.provider.MediaStore.MediaColumns.DISPLAY_NAME, outName);
            values.put(android.provider.MediaStore.MediaColumns.MIME_TYPE, mime);
            values.put(android.provider.MediaStore.MediaColumns.RELATIVE_PATH,
                    android.os.Environment.DIRECTORY_DOWNLOADS);

            android.net.Uri collection = android.provider.MediaStore.Downloads.EXTERNAL_CONTENT_URI;
            android.net.Uri item = getContext().getContentResolver().insert(collection, values);
            if (item == null) throw new java.io.IOException("MediaStore 拒绝了写入 / MediaStore refused");

            try (java.io.OutputStream out = getContext().getContentResolver().openOutputStream(item)) {
                if (out == null) throw new java.io.IOException("无法打开输出流 / cannot open the output");
                out.write(data);
            }
            return android.os.Environment.DIRECTORY_DOWNLOADS + "/" + outName;
        }

        java.io.File dir = android.os.Environment
                .getExternalStoragePublicDirectory(android.os.Environment.DIRECTORY_DOWNLOADS);
        if (!dir.exists() && !dir.mkdirs()) {
            throw new java.io.IOException("无法创建下载目录 / cannot create the Downloads folder");
        }
        java.io.File out = new java.io.File(dir, outName);
        try (java.io.FileOutputStream fos = new java.io.FileOutputStream(out)) {
            fos.write(data);
        }
        return out.getAbsolutePath();
    }

    // ------------------------------------------------------------------
    // 备份:导出 / 导入 — backups: export / import
    // ------------------------------------------------------------------

    /**
     * 把本机服务端生成的备份包写进设备「下载」目录 /
     * write the backup archive produced by this device's server into Downloads.
     *
     * 为什么要绕这一圈:包由**本机服务端**生成(只有它知道 shots_data 在哪),但
     * 落盘必须走原生 —— WebView 不会保存文件,而把 URL 交给系统浏览器会因本 App
     * 退到后台被冻结而拿到空白页(见 savePlugin 那段)。
     *
     * 这一圈并不自相矛盾:请求由原生自己发、自己收,全程在前台,不存在「提供文件的
     * 那一端被冻住」的问题。真正绕开的只有 WebView 存不了文件这一条。
     *
     * Why the round trip: the archive is produced by **this device's own server** (only
     * it knows where shots_data lives), but saving it must go native — a WebView does not
     * save files, and handing the URL to the system browser gets a blank page because
     * this app is frozen in the background while it is the one serving the file (see the
     * savePlugin comment).
     *
     * The round trip is not self-defeating: the native side issues the request and reads
     * the response itself, entirely in the foreground, so nothing that provides the file
     * is ever frozen. The only thing being worked around is a WebView's inability to save
     * a file.
     *
     * @return `{success: boolean, path: string, bytes: number, message: string}`
     */
    @PluginMethod
    public void exportBackup(PluginCall call) {
        // 网络 I/O 不能在主线程做,而且包多大事先不知道,所以另起一个线程。
        // Network I/O cannot run on the main thread and the size is not known up front,
        // so this goes on its own thread.
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    java.net.HttpURLConnection conn = (java.net.HttpURLConnection)
                            new java.net.URL(SELF_BASE + "/api/backup/export").openConnection();
                    conn.setConnectTimeout(10000);
                    conn.setReadTimeout(60000);
                    int code = conn.getResponseCode();
                    if (code != 200) {
                        call.reject("服务端返回 " + code + " / server returned " + code);
                        return;
                    }
                    java.io.ByteArrayOutputStream buf = new java.io.ByteArrayOutputStream();
                    try (java.io.InputStream in = conn.getInputStream()) {
                        byte[] chunk = new byte[8192];
                        int n;
                        while ((n = in.read(chunk)) > 0) buf.write(chunk, 0, n);
                    }
                    byte[] data = buf.toByteArray();

                    String ts = new java.text.SimpleDateFormat("yyyyMMdd_HHmmss", java.util.Locale.US)
                            .format(new java.util.Date());
                    String outName = "printtheshot_backup_" + ts + ".zip";
                    String path = writeToDownloads(outName, data, "application/zip");

                    JSObject ret = new JSObject();
                    ret.put("success", true);
                    ret.put("path", path);
                    ret.put("bytes", data.length);
                    ret.put("message", "已保存到「下载」/ saved to Downloads: " + outName);
                    call.resolve(ret);
                } catch (Exception e) {
                    Log.w(TAG, "导出备份失败 / backup export failed: " + e.getMessage());
                    call.reject("导出失败 / export failed: " + e.getMessage());
                }
            }
        }).start();
    }

    /**
     * 弹系统文件选择器,把选中的备份包交给本机服务端导入 /
     * show the system file picker and hand the chosen archive to the local server.
     *
     * 选择器由原生弹,而不是用 WebView 里的 `<input type=file>`:那里拿到的是一个
     * content:// 影子路径,要读原始字节得绕好几道,还得先把文件复制进 WebView 的
     * 沙箱;原生拿到的就是同一条 content://,直接 openInputStream 就行。
     *
     * The picker is native rather than a WebView `<input type=file>`: there you get a
     * content:// shadow path that takes several detours to read raw bytes from, plus a
     * copy into the WebView's sandbox; natively it is the same URI and openInputStream
     * works directly.
     *
     * @return `{success: boolean, imported: number, cancelled?: boolean, message: string}`
     */
    @PluginMethod
    public void importBackup(PluginCall call) {
        Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        // 放宽到 */* 再自己校验:有些机型/文件管理器对 application/zip 过滤得很严,
        // 而备份包常被识别成 octet-stream,收窄了反而会让用户在自己的文件里找不到它。
        // 选错了会在导入时报错,那也比「列表里根本没有这个文件」好。
        //
        // Widened to */* with validation of our own: some devices and file managers
        // filter application/zip aggressively and a backup archive is often typed as
        // octet-stream, so narrowing it hides the user's own file from them. A wrong
        // pick fails loudly at import time, which beats the file not being listed at all.
        intent.setType("*/*");
        intent.putExtra(Intent.EXTRA_MIME_TYPES,
                new String[]{"application/zip", "application/octet-stream"});
        getBridge().startActivityForPluginWithResult(call, intent, REQ_IMPORT_BACKUP);
    }

    /**
     * 文件选择器回来了 / the file picker came back.
     *
     * `startActivityForPluginWithResult` 把这次的 call 存成「最后一次调用」,所以这里
     * 用 getSavedCall() 取回来 —— 这两个 API 都是 Capacitor 标了 deprecated 的,但
     * 也正是它提供的「等一个 Activity 结果」的机制,没有替代品。
     *
     * `startActivityForPluginWithResult` stashes the call as the last one, so it is
     * retrieved here with getSavedCall(). Both APIs are deprecated in Capacitor, but they
     * are the mechanism it provides for awaiting an Activity result, and there is no
     * replacement.
     */
    @Override
    protected void handleOnActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode != REQ_IMPORT_BACKUP) {
            super.handleOnActivityResult(requestCode, resultCode, data);
            return;
        }
        PluginCall call = getSavedCall();
        if (call == null) {
            Log.w(TAG, "导入结果回来了,但 call 已经不在了 / import result arrived with no call");
            return;
        }
        if (resultCode != android.app.Activity.RESULT_OK || data == null || data.getData() == null) {
            // 用户自己取消的。这不是错误,reject 会在界面上弹一个 ❌。
            // The user cancelled. That is not an error, and rejecting would pop a ❌.
            JSObject ret = new JSObject();
            ret.put("success", false);
            ret.put("cancelled", true);
            ret.put("message", "已取消 / cancelled");
            call.resolve(ret);
            return;
        }

        final Uri uri = data.getData();
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    byte[] body;
                    try (java.io.InputStream in =
                                 getContext().getContentResolver().openInputStream(uri)) {
                        if (in == null) {
                            throw new java.io.IOException("打不开这个文件 / cannot open the file");
                        }
                        java.io.ByteArrayOutputStream buf = new java.io.ByteArrayOutputStream();
                        byte[] chunk = new byte[8192];
                        int n;
                        while ((n = in.read(chunk)) > 0) buf.write(chunk, 0, n);
                        body = buf.toByteArray();
                    }

                    // 原始字节直接 POST 给本机服务端,不做表单编码 —— content-type
                    // 不是 multipart 时服务端就把 body 本身当 zip 收。
                    //
                    // The raw bytes go straight to the local server with no form encoding:
                    // when the content-type is not multipart the server treats the body
                    // itself as the zip.
                    java.net.HttpURLConnection conn = (java.net.HttpURLConnection)
                            new java.net.URL(SELF_BASE + "/api/backup/import").openConnection();
                    conn.setRequestMethod("POST");
                    conn.setDoOutput(true);
                    conn.setConnectTimeout(10000);
                    conn.setReadTimeout(120000);
                    conn.setRequestProperty("Content-Type", "application/zip");
                    conn.setFixedLengthStreamingMode(body.length);
                    try (java.io.OutputStream out = conn.getOutputStream()) {
                        out.write(body);
                    }

                    int code = conn.getResponseCode();
                    java.io.InputStream src = (code >= 200 && code < 300)
                            ? conn.getInputStream() : conn.getErrorStream();
                    java.io.ByteArrayOutputStream rbuf = new java.io.ByteArrayOutputStream();
                    if (src != null) {
                        try (java.io.InputStream in = src) {
                            byte[] chunk = new byte[8192];
                            int n;
                            while ((n = in.read(chunk)) > 0) rbuf.write(chunk, 0, n);
                        }
                    }
                    JSONObject res = new JSONObject(new String(rbuf.toByteArray(),
                            java.nio.charset.StandardCharsets.UTF_8));

                    JSObject ret = new JSObject();
                    ret.put("success", res.optBoolean("success", false));
                    ret.put("imported", res.optInt("imported", 0));
                    ret.put("message", res.optString("message", ""));
                    call.resolve(ret);
                } catch (Exception e) {
                    Log.w(TAG, "导入备份失败 / backup import failed: " + e.getMessage());
                    call.reject("导入失败 / import failed: " + e.getMessage());
                }
            }
        }).start();
    }

    // ------------------------------------------------------------------
    // 打印 / printing
    // ------------------------------------------------------------------

    /**
     * 打印一份 base64 的 ESC/POS 完整字节流 / print a complete base64 ESC/POS byte stream.
     *
     * @return `{success: boolean, message: String}`
     */
    @PluginMethod
    public void printRaw(final PluginCall call) {
        final String data = call.getString("data");
        final String address = call.getString("address");

        if (data == null || data.isEmpty()) {
            call.reject("printRaw 需要 base64 的 data 参数 / printRaw needs a base64 'data' argument");
            return;
        }

        final byte[] payload;
        try {
            payload = Base64.decode(data, Base64.DEFAULT);
        } catch (IllegalArgumentException e) {
            call.reject("data 不是合法的 base64 / 'data' is not valid base64");
            return;
        }
        if (payload.length == 0) {
            call.reject("data 解码后为空 / 'data' decoded to an empty payload");
            return;
        }

        final BluetoothPrinter printer = BluetoothPrinter.get();
        printer.executor().execute(new Runnable() {
            @Override
            public void run() {
                // printJob 会按需切换/重连到目标打印机,数据本身原样写出 /
                // printJob switches or reconnects to the target printer as
                // needed and writes the payload verbatim
                boolean ok = printer.printJob(payload, address);
                JSObject ret = new JSObject();
                ret.put("success", ok);
                ret.put("message", ok
                        ? "已发送 " + payload.length + " 字节 / sent " + payload.length + " bytes"
                        : printer.getLastError());
                call.resolve(ret);
            }
        });
    }

    // ------------------------------------------------------------------
    // 前台服务(后台保活)/ foreground service (keep-alive)
    // ------------------------------------------------------------------

    /**
     * 起前台服务 / start the foreground service.
     *
     * App 启动时会自己调一次(见 MainActivity),这里是给界面上的开关用的 ——
     * 关掉之后可以再打开,不必重启 App。
     * Called automatically when the app starts (see MainActivity); this entry point
     * exists so the UI switch can turn it back on without restarting the app.
     *
     * 常驻的代价是电量和一条常驻通知。类型是 connectedDevice 而不是 dataSync,
     * 所以没有 Android 15 那个每日 6 小时的上限,可以一直开着。
     * Residency costs battery and one persistent notification. The type is
     * connectedDevice rather than dataSync, so Android 15's 6-hour daily cap does not
     * apply and it can stay up.
     *
     * @return `{success: boolean, running: boolean}`
     */
    @PluginMethod
    public void startForegroundService(PluginCall call) {
        boolean ok = PrinterService.start(getContext());
        JSObject ret = new JSObject();
        ret.put("success", ok);
        ret.put("running", PrinterService.isRunning());
        call.resolve(ret);
    }

    /**
     * 停前台服务 / stop the foreground service.
     *
     * @return `{success: boolean, running: boolean}`
     */
    @PluginMethod
    public void stopForegroundService(PluginCall call) {
        PrinterService.stop(getContext());
        JSObject ret = new JSObject();
        ret.put("success", true);
        ret.put("running", PrinterService.isRunning());
        call.resolve(ret);
    }
}
