package com.printtheshot.app;

import android.Manifest;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Bundle;
import android.util.Log;
import android.webkit.WebView;

import androidx.core.content.ContextCompat;

import com.getcapacitor.Bridge;
import com.getcapacitor.BridgeActivity;
import com.printtheshot.printer.BluetoothPrinterBridge;
import com.printtheshot.printer.PrintTheShotPrinterPlugin;
import com.printtheshot.printer.PrinterService;
import com.printtheshot.server.DeviceInfo;
import com.printtheshot.server.ServerHolder;

/**
 * App 入口 Activity / the app's entry activity
 * ==============================================================================
 *
 * 中文
 * ----
 * Capacitor 生成出来的 MainActivity 只有一句 super.onCreate()。这里多两件事:
 *
 *   1) registerPlugin(...) —— 本插件是项目内的本地插件(不是 npm 包),Capacitor
 *      不会自动发现它,必须显式注册。少这一句,JS 侧
 *      `Capacitor.Plugins.PrintTheShotPrinter` 会是 undefined,而且不报错,是
 *      最容易踩的坑。
 *   2) BluetoothPrinterBridge.attach(...) —— 把应用上下文交给蓝牙层,这样
 *      pyjnius / 本机 HTTP 桥这些非 Capacitor 的入口也能直接用,不必等插件被
 *      调用过。传 getApplicationContext() 而不是 this,避免持有 Activity。
 *
 * 注意 registerPlugin 要在 super.onCreate() **之前**调用。
 *
 * English
 * -------
 * The generated MainActivity only has a super.onCreate() call. Two additions:
 *
 *   1) registerPlugin(...) — this plugin is a local plugin inside the project
 *      rather than an npm package, so Capacitor will not discover it on its
 *      own. Without this line `Capacitor.Plugins.PrintTheShotPrinter` is simply
 *      undefined in JS, with no error: the easiest trap in this setup.
 *   2) BluetoothPrinterBridge.attach(...) — hands the application context to
 *      the Bluetooth layer so non-Capacitor entry points (pyjnius, the local
 *      HTTP bridge) work without waiting for a plugin call. The application
 *      context is used rather than `this`, so no activity is retained.
 *
 * Note that registerPlugin must be called **before** super.onCreate().
 */
public class MainActivity extends BridgeActivity {

    private static final String TAG = "PrintTheShot";

    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(PrintTheShotPrinterPlugin.class);
        super.onCreate(savedInstanceState);

        // 上下文先给上,后面谁先来都能用 / prime the context for whoever runs first
        BluetoothPrinterBridge.attach(getApplicationContext());

        // 启动内置 HTTP 服务 —— 平板自己就是服务端,DE1 直接传 JSON 过来
        // Start the built-in HTTP server: the tablet IS the server the DE1 uploads to
        startServer();

        // 服务端收到 shot 时叫醒 WebView 去渲染并打印。必须在 super.onCreate() 之后
        // 注册 —— 那时 getBridge() 才不是 null。
        //
        // Wake the WebView when the server receives a shot. This has to happen after
        // super.onCreate(), which is when getBridge() stops being null.
        registerWakeHook();

        // 拉起前台服务。没有它,App 一退到后台就会被 Android 冻结:服务端收不到
        // 上传、WebView 不渲染,整条链路静默断掉。而这个 App 的正常形态恰恰就是
        // 「Decaid 在前台,它待在后台等 shot」—— 所以这里是自动的,不是可选项。
        //
        // Start the foreground service. Without it the app is frozen the moment it
        // goes to the background: the server stops receiving, the WebView stops
        // rendering, and the whole chain fails silently. Sitting in the background
        // while Decaid is in the foreground *is* this app's normal shape, so this is
        // automatic rather than optional.
        startKeepAlive();
    }

    /**
     * 权限授予后回到界面时再试一次 / retry once the permission has been granted.
     *
     * 蓝牙权限是异步授予的(界面上的蓝牙设置页会申请),授予之后 App 会被暂停再恢复,
     * 所以这里是个自然的重试点。已经开着时 startKeepAlive 什么都不做。
     *
     * The Bluetooth permission is granted asynchronously — the Bluetooth settings screen
     * asks for it — and granting it pauses and resumes the activity, which makes this a
     * natural place to retry. startKeepAlive does nothing when the service is already up.
     */
    @Override
    public void onResume() {
        super.onResume();
        startKeepAlive();
    }

    /**
     * 拉起后台常驻 / bring up the background keep-alive.
     *
     * 失败不阻断启动:界面照常能用,只是退到后台会被冻结。界面上那个开关会显示
     * 实际状态,用户还能手动再试一次。
     *
     * A failure here must not block startup: the UI still works, it just gets frozen
     * when backgrounded. The UI switch reflects the real state and can retry by hand.
     *
     * 先说权限:connectedDevice 这个前台服务类型要求**已授予的**蓝牙权限之一
     * (BLUETOOTH_CONNECT 等),而它们是运行时权限 —— 全新安装后第一次启动时一个
     * 都没有。不先看一眼就直接起,服务会在 startForeground 抛 SecurityException,
     * 而那是 App 启动路径上的调用,结果是**启动即闪退**,用户连界面都看不到。
     *
     * The permission check comes first: the connectedDevice foreground service type
     * requires one of the Bluetooth permissions to be **granted**, and those are runtime
     * permissions — a fresh install has none. Starting regardless makes startForeground
     * throw SecurityException on the app's startup path, and the result is **a crash on
     * launch**, before the user sees anything.
     */
    private void startKeepAlive() {
        if (PrinterService.isRunning()) return;

        // Android 12 (API 31) 起 BLUETOOTH_CONNECT 才是运行时权限,之前不存在
        // BLUETOOTH_CONNECT only became a runtime permission in Android 12 (API 31)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S
                && ContextCompat.checkSelfPermission(this, Manifest.permission.BLUETOOTH_CONNECT)
                   != PackageManager.PERMISSION_GRANTED) {
            Log.i(TAG, "蓝牙权限未授予,后台常驻暂不启动;授权后回到界面会自动开启 / "
                    + "BLUETOOTH_CONNECT not granted; keep-alive deferred until it is");
            return;
        }

        if (PrinterService.start(this)) {
            Log.i(TAG, "后台常驻已开启 / keep-alive on");
        } else {
            Log.w(TAG, "后台常驻未启动,退到后台会被系统冻结 / keep-alive failed; "
                    + "the app will be frozen in the background");
        }
    }

    /**
     * 把「WebView 可以被叫醒」这件事告诉服务端 / let the server know it can poke the WebView.
     *
     * 为什么要这样绕一圈:App 退到后台时,前端那条 `setInterval(pumpQueue, 5000)` 会
     * 被 Chromium 按「隐藏页面」节流到大约一分钟一次,甚至有的一觉不醒 —— 于是
     * 队列没人取,打印就停了。服务端却始终知道 shot 刚到,所以由它来推一把。
     *
     * 唤醒走 evaluateJavascript,不走定时器,因此不受节流影响。渲染仍然只发生在
     * 前端(那张离屏 canvas 在后台照样能画),协议也仍然只在 web/printer.js 里拼 ——
     * 叫醒的是同一个前端,不是另起一个渲染实现。
     *
     * Why the indirection: in the background the front end's
     * `setInterval(pumpQueue, 5000)` is throttled by Chromium as a hidden page — down
     * to about once a minute, sometimes never. Nobody drains the queue, so printing
     * stops. The server, though, always knows a shot has just landed, so it does the
     * poking.
     *
     * The poke is an evaluateJavascript call, not a timer, so throttling does not
     * apply. Rendering still happens only in the front end — that offscreen canvas
     * draws fine in the background — and the protocol is still assembled only in
     * web/printer.js. It wakes the same front end; it does not add a second renderer.
     */
    private void registerWakeHook() {
        ServerHolder.setWakeWebView(() -> {
            Bridge bridge = getBridge();
            if (bridge == null) return;
            WebView webView = bridge.getWebView();
            if (webView == null) return;

            // 这里可能已经在主线程上(Handler.post 回来的),但 WebView 也要求主线程,
            // 所以再 post 一次是无害的,并且能兜住直接从别处调用的情况。
            //
            // This is normally already on the main thread (posted back by the Handler),
            // but a WebView demands the main thread, so posting again is harmless and
            // covers being called from anywhere else.
            webView.post(() -> webView.evaluateJavascript(PUMP_JS, null));
        });
    }

    /**
     * 叫前端取一次队列 / ask the front end to drain the queue once.
     *
     * 前端没加载完时这些全局量还不存在,所以整句都做了存在性判断 —— 否则后台收到
     * 上传会在控制台刷一片 TypeError,而真正的问题(界面还没准备好)反而看不见。
     *
     * The globals do not exist until the front end has loaded, hence the guards — an
     * upload arriving early would otherwise spray TypeErrors and bury the real cause.
     */
    private static final String PUMP_JS =
            "window.PrintTheShotPrinter"
                    + " && PrintTheShotPrinter.pumpQueue"
                    + " && PrintTheShotPrinter.pumpQueue();";

    /**
     * 启动内置服务并在日志里打出访问地址 / start the server and log where to reach it.
     *
     * 为什么要打日志:界面里会显示局域网地址,但你得先能进到界面。服务起不来
     * (端口被占之类)时界面根本加载不出来,那时日志是唯一的线索。
     *
     * Why the log line: the UI shows the LAN address, but you have to reach the UI
     * first. When the server fails to start — port taken, say — the UI never loads,
     * and the log is the only clue.
     */
    private void startServer() {
        if (ServerHolder.start(this)) {
            String url = DeviceInfo.lanUrl(ServerHolder.PORT);
            Log.i(TAG, "服务已启动 / server running: "
                    + (url.isEmpty() ? "未检测到局域网地址 / no LAN address" : url));
        } else {
            Log.e(TAG, "服务启动失败,端口 " + ServerHolder.PORT
                    + " 可能被占用 / failed to start; the port may be in use");
        }
    }
}
