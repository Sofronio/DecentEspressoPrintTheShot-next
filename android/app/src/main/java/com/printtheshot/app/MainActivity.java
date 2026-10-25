package com.printtheshot.app;

import android.Manifest;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.util.Base64;
import android.util.Log;
import android.webkit.WebView;

import androidx.core.content.ContextCompat;

import com.getcapacitor.Bridge;
import com.getcapacitor.BridgeActivity;
import com.printtheshot.printer.BluetoothPrinter;
import com.printtheshot.printer.BluetoothPrinterBridge;
import com.printtheshot.printer.PrintTheShotPrinterPlugin;
import com.printtheshot.printer.PrinterService;
import com.printtheshot.server.DeviceInfo;
import com.printtheshot.server.ServerHolder;

import org.json.JSONObject;

import java.util.concurrent.atomic.AtomicBoolean;

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

        // App 在后台时的同步打印路径。和 wake 分开:那条是异步泵,页面一冻结
        // 就卡住;这条用一次同步渲染 + 原生写蓝牙。
        //
        // The synchronous background path. Separate from wake: that one is the async
        // pump and stalls as soon as the page freezes; this one does one synchronous
        // render and a native Bluetooth write.
        registerBackgroundPrinter();

        // 下载交给系统浏览器 / hand downloads to the system browser
        routeDownloadsToBrowser();

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
        ServerHolder.setUiVisible(true);
        startKeepAlive();
    }

    /**
     * 界面离开前台后,新任务改走后台上报路径 / once the UI leaves the foreground, new
     * jobs use the background path.
     *
     * 用 onPause 而不是 onStop:锁屏时 onStop 不一定立刻来,而 onPause 会。界面
     * 部分可见(比如权限弹窗)时也可能触发,但那时后台路径同样能完成打印,只是
     * 让前端异步泵少一条路;任务认领保证不会两边都打。
     *
     * onPause rather than onStop: screen-off does not always call onStop promptly,
     * while onPause does. A partially visible UI (a permission dialog) may also
     * trigger it, but the background path still completes the print then; the claim
     * keeps the two paths from printing the same job twice.
     */
    @Override
    public void onPause() {
        ServerHolder.setUiVisible(false);
        super.onPause();
    }

    @Override
    public void onDestroy() {
        ServerHolder.setUiVisible(false);
        ServerHolder.setBackgroundPrinter(null);
        super.onDestroy();
    }

    /**
     * 把 WebView 里的下载交给系统浏览器 / hand WebView downloads to the system browser.
     *
     * 问题:WebView **不会自己保存文件**。界面上的「下载插件」前两个按钮是
     * `<a href="/plugin/plugin.tcl" download>`,在桌面浏览器里正常,在 APK 里点了
     * 没有任何反应 —— 没有报错,也没有文件,因为没人处理下载这件事。第三个(TXT)
     * 看起来「能打开」只是因为带着 .txt 的地址被当成一次普通导航,浏览器能显示它,
     * 但那不是下载,不会存到任何地方。
     *
     * Capacitor 自己也没有设 DownloadListener(本项目用的 6.x),所以要在原生侧接:
     * 拿到下载 URL 转给系统浏览器,由它去取、去存。地址是 http://localhost:8000/...,
     * 也就是这个 App 自己起的服务 —— 浏览器在同一个设备上,够得着。
     *
     * The problem: a WebView **does not save files on its own**. The first two plugin
     * download buttons are `<a href="/plugin/plugin.tcl" download>`, which works in a
     * desktop browser and does nothing at all inside an APK — no error and no file,
     * because nothing handles the download. The third (TXT) only appears to "open"
     * because a .txt URL is treated as ordinary navigation: the browser can display it,
     * but that is not a download and nothing is stored anywhere.
     *
     * Capacitor (6.x, what this project uses) sets no DownloadListener either, so it has
     * to be caught natively: take the download URL and hand it to the system browser to
     * fetch and save. The URL is http://localhost:8000/... — this app's own server, and
     * the browser is on the same device, so it can reach it.
     */
    private void routeDownloadsToBrowser() {
        Bridge bridge = getBridge();
        if (bridge == null) return;
        WebView webView = bridge.getWebView();
        if (webView == null) return;

        webView.setDownloadListener((url, userAgent, contentDisposition, mimeType, contentLength) -> {
            if (url == null || url.isEmpty()) return;
            try {
                Intent view = new Intent(Intent.ACTION_VIEW, Uri.parse(url));
                view.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                startActivity(view);
                Log.i(TAG, "下载已转交浏览器 / download handed to the browser: " + url);
            } catch (Exception e) {
                // 设备上没有任何浏览器能处理这个 URL。说清楚,别让它变成一个
                // 「点了没反应」的静默失败。
                // No browser on the device can handle the URL. Say so, rather than
                // leaving it as a silent "I tapped it and nothing happened".
                Log.w(TAG, "没有应用能打开这个下载 / nothing can open this download: "
                        + e.getMessage());
            }
        });
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
     * **实测(2026-10-25,三星 SM-X210 / Android 16):这条只叫得醒同步入口。**
     * 页面隐藏后 setTimeout / fetch / Promise 续段会被冻结,evaluateJavascript
     * 能执行 pumpQueue 的第一段同步代码,但它卡在第一个 `await fetchQueue()`,
     * 直到 App 回到前台才继续。所以后台打印**不靠这条**:registerBackgroundPrinter()
     * 走的是「一次同步渲染 + 原生写蓝牙」。wake 只在前台、或页面尚未冻结时让
     * 打印快一点。
     *
     * 渲染和协议仍然只有一份(web/printer.js);后台路径复用的也是它。
     *
     * Why the indirection: in the background the front end's
     * `setInterval(pumpQueue, 5000)` is throttled by Chromium as a hidden page — down
     * to about once a minute, sometimes never. Nobody drains the queue, so printing
     * stops. The server, though, always knows a shot has just landed, so it does the
     * poking.
     *
     * **Measured (SM-X210 / Android 16, 2026-10-25): the poke only runs the synchronous
     * entry.** Once the page is hidden, setTimeout / fetch / promise continuations are
     * frozen, and pumpQueue stalls on its first `await fetchQueue()` until the app is
     * foregrounded. Background printing therefore does **not** rely on this hook:
     * registerBackgroundPrinter() uses one synchronous render plus a native Bluetooth
     * write. wake only makes printing a little faster in the foreground, or before the
     * page has frozen.
     *
     * There is still exactly one renderer and one protocol (web/printer.js); the
     * background path reuses them.
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
     * 注册后台同步打印 / register the synchronous background print path.
     *
     * 和 wake 的区别:页面在后台被冻结时,异步的 fetch/await 不会继续,但
     * evaluateJavascript 注入的**同步**脚本会执行。所以这里一次脚本里完成
     * 渲染和 ESC/POS 拼装,结果通过回调拿回 Java,再由前台服务直接写蓝牙。
     *
     * 绘制的实现仍然只有 web/render.js 和 web/printer.js 这一份;Java 不做版面,
     * 也不拼 ESC/POS。
     *
     * How it differs from wake: a hidden page's async fetch/await never resumes, but a
     * **synchronous** script injected with evaluateJavascript still runs. So one script
     * does the render and ESC/POS assembly, the result comes back to Java through the
     * callback, and the foreground service writes it to Bluetooth. Drawing still lives
     * only in web/render.js and web/printer.js; Java does neither layout nor ESC/POS.
     */
    private void registerBackgroundPrinter() {
        ServerHolder.setBackgroundPrinter((filename, shotJson, configJson, machineId, done) -> {
            Bridge bridge = getBridge();
            WebView webView = bridge == null ? null : bridge.getWebView();
            if (webView == null) {
                done.onDone(false, "no webview");
                return;
            }

            // 渲染回调如果一直不回来(页面被销毁、脚本异常),到点释放认领,
            // 让前台路径处理;否则任务会永久躲在 claimed 状态里。
            //
            // If the render callback never arrives (dead page, script error), release
            // the claim on a timer; otherwise the job would stay hidden in "claimed".
            final AtomicBoolean finished = new AtomicBoolean(false);
            final Runnable timeout = () -> {
                if (finished.compareAndSet(false, true)) {
                    Log.w(TAG, "后台渲染超时 / background render timed out: " + filename);
                    done.onDone(false, "timeout");
                }
            };
            webView.postDelayed(timeout, 8000);

            // JSON 用 quote 之后交给 JSON.parse:shot 内容里可能有引号、换行、
            // 甚至 U+2028,直接拼进 JS 会变成语法错误或截断字符串。
            //
            // Quote the JSON and parse it inside JS: shot text can contain quotes,
            // newlines or U+2028, which break a raw splice into the script.
            String js = "(function(){try{"
                    + "var shot=JSON.parse(" + JSONObject.quote(shotJson) + ");"
                    + "var cfg=JSON.parse(" + JSONObject.quote(configJson) + ");"
                    + "return PrintTheShotPrinter.renderJobSync(shot,{config:cfg,machineId:"
                    + JSONObject.quote(machineId == null ? "" : machineId) + "});"
                    + "}catch(e){return 'ERR:'+(e&&e.message||e);}})()";

            webView.post(() -> webView.evaluateJavascript(js, result -> {
                if (!finished.compareAndSet(false, true)) return;
                webView.removeCallbacks(timeout);

                String value = decodeJsString(result);
                if (value == null || value.startsWith("ERR:") || "NOT_READY".equals(value)) {
                    Log.w(TAG, "后台渲染失败 / background render failed: "
                            + filename + " — " + value);
                    done.onDone(false, value == null ? "bad result" : value);
                    return;
                }

                final byte[] payload;
                try {
                    payload = Base64.decode(value, Base64.DEFAULT);
                } catch (IllegalArgumentException e) {
                    done.onDone(false, "bad base64");
                    return;
                }

                final BluetoothPrinter printer = BluetoothPrinter.get();
                if (printer == null) {
                    done.onDone(false, "printer not ready");
                    return;
                }
                // 蓝牙写在打印线程上,不占 WebView 主线程;前台服务保证进程还活着。
                // The Bluetooth write runs on the printer thread, off the WebView main
                // thread; the foreground service keeps the process alive.
                printer.executor().execute(() -> {
                    boolean ok = printer.printJob(payload, printer.getDefaultAddress());
                    String message = ok
                            ? "sent " + payload.length + " bytes"
                            : printer.getLastError();
                    Log.i(TAG, "后台打印 / background print " + filename + ": " + message);
                    done.onDone(ok, message);
                });
            }));
        });
    }

    /**
     * evaluateJavascript 的结果是一个 JSON 字符串字面量,这里把它解出来。
     * The evaluateJavascript result is a JSON string literal; unwrap it.
     */
    private static String decodeJsString(String result) {
        if (result == null || "null".equals(result)) return null;
        try {
            Object value = new org.json.JSONTokener(result).nextValue();
            return value instanceof String ? (String) value : null;
        } catch (Exception e) {
            return null;
        }
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
