package com.printtheshot.app;

import android.os.Bundle;
import android.util.Log;

import com.getcapacitor.BridgeActivity;
import com.printtheshot.printer.BluetoothPrinterBridge;
import com.printtheshot.printer.PrintTheShotPrinterPlugin;
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
    }

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
