package com.printtheshot.printer;

import android.content.Context;
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
