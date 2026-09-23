package com.printtheshot.server;

import android.content.Context;
import android.util.Log;

import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 内置服务端的单例 / the built-in server singleton
 * ================================================
 *
 * 中文
 * ----
 * 把平板上那个 HTTP 服务的生命周期收在一个地方,避免 Activity 重建时起两个服务
 * (那会导致端口冲突,而症状是「用着用着就连不上了」,很难查)。
 *
 * 服务绑 0.0.0.0,所以任何持有 Context 的组件都能取到它 —— 目前是 MainActivity
 * 启动它,本机 HTTP 桥(LocalPrintBridge)也走同一个实例。
 *
 * English
 * -------
 * Keeps the tablet's HTTP service lifecycle in one place so an Activity recreation
 * cannot start a second one (that would collide on the port, and the symptom —
 * "it stops connecting after a while" — is hard to trace).
 *
 * The server binds 0.0.0.0, so any component with a Context can reach it. MainActivity
 * starts it; the local HTTP bridge (LocalPrintBridge) shares the same instance.
 */
public final class ServerHolder {

    private static final String TAG = "PTSServerHolder";

    /** 与桌面端一致的端口。DE1 插件/用户都按 8000 配,换端口会让人困惑。 */
    /** The same port as the desktop side. The DE1 plugin and users assume 8000. */
    public static final int PORT = 8000;

    private static MiniHttpServer server;
    private static ShotStore store;

    /**
     * WebView 的唤醒入口,由 MainActivity 在界面就绪后注册 / the WebView wake hook,
     * registered by MainActivity once the UI exists.
     *
     * 服务比界面先起来是正常的(服务在 onCreate 里启动,WebView 要晚一点才可用),
     * 所以这里允许为空 —— 为空时上传照常入队,只是没人被叫醒,等前台定时器来取。
     *
     * The server legitimately starts before the UI does — it is started in onCreate
     * while the WebView becomes usable a little later — so this may be null. When it
     * is, an upload still queues normally; nobody is poked and the foreground timer
     * picks it up instead.
     *
     * volatile:写发生在主线程,读发生在 HTTP 线程 / written on the main thread and
     * read on an HTTP thread.
     */
    private static volatile Runnable wakeWebView;

    private ServerHolder() {
    }

    /**
     * 注册 WebView 唤醒入口 / register the WebView wake hook.
     *
     * Activity 每次重建都会重新调一次,所以这里不做「已经注册过就跳过」的判断 ——
     * 旧的那个 Runnable 捕获的是已经销毁的 Activity,留着它反而会往死掉的 WebView
     * 上发消息。
     *
     * Called again on every Activity recreation, so there is deliberately no
     * "already registered" guard: the previous runnable captured an Activity that no
     * longer exists, and keeping it would post into a dead WebView.
     */
    public static void setWakeWebView(Runnable wake) {
        wakeWebView = wake;
    }

    public static synchronized boolean start(Context ctx) {
        if (server != null && server.isRunning()) return true;

        Context app = ctx.getApplicationContext();
        store = new ShotStore(app);

        String version = readVersion(app);
        // 传的是取值器而不是当前值:服务比界面起得早,wakeWebView 那时还是 null,
        // 直接读一次会把 null 永久钉进 AppServer。
        //
        // Pass a reader rather than the current value: the server starts before the UI,
        // so wakeWebView is still null here, and reading it once would pin that null
        // into AppServer forever.
        server = new MiniHttpServer(PORT, new AppServer(app, store, version,
                () -> {
                    Runnable w = wakeWebView;
                    if (w != null) w.run();
                }));
        boolean ok = server.start();
        if (!ok) {
            Log.e(TAG, "服务启动失败(端口 " + PORT + " 可能被占用)/ failed to start on port " + PORT);
            server = null;
        }
        return ok;
    }

    public static synchronized void stop() {
        if (server != null) {
            server.stop();
            server = null;
        }
    }

    public static synchronized boolean isRunning() {
        return server != null && server.isRunning();
    }

    public static synchronized ShotStore store() {
        return store;
    }

    /**
     * 版本号从打包进 APK 的 strings.js 里读 / read the version out of the bundled strings.js.
     *
     * 那份文件由 scripts/export_strings.py 从 print_the_shot_server.py 的 VERSION
     * 生成,前端也在用同一个值。在这里再写一个 Java 常量就意味着多一个需要人工同步
     * 的地方 —— 而版本号不同步的症状是「界面显示的版本和实际不符」,不疼不痒,
     * 于是永远没人去修。
     *
     * That file is generated from VERSION in print_the_shot_server.py by
     * scripts/export_strings.py, and the front end reads the same value. A Java
     * constant here would be one more thing to keep in sync by hand — and a stale
     * version shows up as "the UI says a different version than it is", which is
     * harmless enough that nobody ever fixes it.
     */
    private static String readVersion(Context ctx) {
        try (InputStream in = ctx.getAssets().open("public/strings.js")) {
            byte[] buf = new byte[8192];
            int n = in.read(buf);
            if (n <= 0) return "unknown";
            String head = new String(buf, 0, n, StandardCharsets.UTF_8);
            Matcher m = Pattern.compile("window\\.PTS_VERSION\\s*=\\s*\"([^\"]+)\"").matcher(head);
            if (m.find()) return m.group(1);
        } catch (Exception e) {
            Log.w(TAG, "读取版本号失败 / could not read the version: " + e.getMessage());
        }
        return "unknown";
    }
}
