package com.printtheshot.app;

import android.os.Bundle;

import com.getcapacitor.BridgeActivity;
import com.printtheshot.printer.BluetoothPrinterBridge;
import com.printtheshot.printer.PrintTheShotPrinterPlugin;

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

    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(PrintTheShotPrinterPlugin.class);
        super.onCreate(savedInstanceState);

        // 上下文先给上,后面谁先来都能用 / prime the context for whoever runs first
        BluetoothPrinterBridge.attach(getApplicationContext());
    }
}
