package com.printtheshot.server;

import android.content.Context;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;

import com.printtheshot.printer.BluetoothPrinter;
import com.printtheshot.printer.PrinterService;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Locale;

/**
 * 平板上跑的完整服务端 / the complete server running on the tablet
 * ================================================================
 *
 * 中文
 * ----
 * 把平板变成一台独立的打印节点:DE1 把 shot JSON 传到它,它渲染(在 WebView 里)
 * 并通过蓝牙打印,不需要任何电脑参与。
 *
 * 对外提供的接口和桌面版 Python 服务端保持一致 —— 这样同一份 Web UI、同一个
 * DE1 插件配置,两边都能用。
 *
 * 范围:只做核心打印链路(Web UI、上传、历史、统计、待打印队列、打印机列表、
 * 打印分发)。AI 翻译、在线更新、插件分发、多语言管理这些桌面端功能没有实现,
 * 相应的接口返回一个**说得清楚的空结果**,而不是 404 —— 前端会调它们,而
 * "接口不存在"和"这个功能在这端没有"是两回事,前者会让界面报错。
 *
 * English
 * -------
 * Turns the tablet into a self-contained printing node: the DE1 uploads shot JSON to
 * it, it renders (in the WebView) and prints over Bluetooth, with no computer
 * involved.
 *
 * The API mirrors the desktop Python server so the same web UI and the same DE1
 * plugin configuration work against either.
 *
 * Scope: the core printing chain only (web UI, upload, history, statistics, the
 * pending-print queue, the printer list, print dispatch). AI translation, self-update,
 * plugin distribution and language management are desktop concerns and are not
 * implemented here; those endpoints return a **clear empty result** rather than a 404,
 * because the front end does call them and "no such endpoint" is not the same message
 * as "this build does not have that feature" — the former makes the UI report errors.
 */
public class AppServer implements MiniHttpServer.Handler {

    private static final String TAG = "PTSAppServer";
    private static final String ASSET_ROOT = "public";

    private final Context ctx;
    private final ShotStore store;
    private final String version;

    /**
     * 有新 shot 入队时叫醒 WebView 去渲染 / poke the WebView when a shot is queued.
     *
     * 为什么需要它:前端那条 `setInterval(pumpQueue, 5000)` 在 App 退到后台后会被
     * Chromium 节流(隐藏页面),长时间后台约一分钟才轮到一次,甚至有的一觉不醒。
     * 而服务端**始终知道自己刚收到了东西** —— 于是由它主动推一把,不依赖定时器。
     *
     * 注意 Capacitor 本身不是元凶:它的 keepRunning 默认为 true,不会调
     * pauseTimers()。冻结来自浏览器的隐藏页面节流。
     *
     * Why: the front end's `setInterval(pumpQueue, 5000)` is throttled once the app
     * goes to the background — Chromium throttles timers on hidden pages, down to
     * roughly once a minute, and sometimes not at all. The server, however, *always*
     * knows the moment something arrives, so it does the poking instead of relying
     * on a timer.
     *
     * Capacitor is not the culprit: its keepRunning preference defaults to true, so
     * it never calls pauseTimers(). The freezing comes from hidden-page throttling.
     */
    private final Runnable wake;

    private final Handler main = new Handler(Looper.getMainLooper());

    /** 进程启动时刻,对应桌面端的 start_time / process start time. */
    private static final String _startedAt =
            new java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US)
                    .format(new java.util.Date());

    public AppServer(Context ctx, ShotStore store, String version, Runnable wake) {
        this.ctx = ctx;
        this.store = store;
        this.version = version;
        this.wake = wake;
    }

    /**
     * 叫醒前端去取队列 / tell the front end to drain the queue.
     *
     * WebView 只能在主线程上碰,而上传是在 HTTP 线程上处理的,所以这里必须切回去。
     * 没有注册唤醒方(比如服务先于界面起来)就什么都不做 —— 那种情况下前台的
     * 定时器仍然会把队列取走。
     *
     * A WebView may only be touched from the main thread, and uploads are handled on
     * an HTTP thread, so this has to hop back. With no wake runnable registered — the
     * server can start before the UI exists — it does nothing; the foreground timer
     * still drains the queue in that case.
     */
    private void wakeFrontEnd() {
        if (wake == null) return;
        main.post(wake);
    }

    @Override
    public MiniHttpServer.Response handle(MiniHttpServer.Request req) throws Exception {
        String p = req.path;

        // CORS 预检 / CORS preflight
        if ("OPTIONS".equals(req.method)) {
            return new MiniHttpServer.Response();   // 204 + 上面的 CORS 头
        }

        // ---- API ----
        if (p.startsWith("/api/")) return handleApi(req, p);

        // ---- 上传 ----
        if (p.equals("/upload")) return handleUpload(req);

        // ---- JSON 下载 ----
        if (p.startsWith("/download/json/")) {
            File f = store.fileFor(p.substring("/download/json/".length()));
            if (f == null || !f.exists()) return MiniHttpServer.Response.text(404, "not found");
            MiniHttpServer.Response r = MiniHttpServer.Response.bytes(200, "application/json; charset=utf-8", readFile(f));
            r.extraHeaders.put("Content-Disposition", "attachment; filename=\"" + f.getName() + "\"");
            return r;
        }

        // ---- 静态资源(Web UI)----
        return serveAsset(p);
    }

    // ========================================================================
    // 静态资源 / static assets
    // ========================================================================

    /**
     * 从 APK 资源里取文件 / serve a file out of the APK assets.
     *
     * 只做 basename 之后的拼接,并且逐个校验存在性 —— assets 目录没有真正的
     * "路径",但 `..` 仍然可能拼出一个不存在的名字,统一挡住更省心。
     *
     * 这里**不做模板替换**。index.html 的 {{VERSION}} 和 app.js 的 {{LANG}} 由前端
     * 自己兜底(strings.js + resolveStrings() + 标题补丁),已经在浏览器与 APK
     * 两条路径上验证过。Java 这边再实现一遍没有意义,只会多一个会走偏的地方。
     *
     * No template substitution here. The {{VERSION}} in index.html and the {{LANG}} in
     * app.js are handled by the front end itself (strings.js, resolveStrings(), and the
     * title patch), already verified on both the browser and APK paths. Doing it again
     * in Java would add a second place to drift.
     */
    private MiniHttpServer.Response serveAsset(String path) {
        String name = path;
        if (name.startsWith("/")) name = name.substring(1);
        if (name.isEmpty()) name = "index.html";
        if (name.contains("..")) return MiniHttpServer.Response.text(403, "forbidden");

        String assetPath = ASSET_ROOT + "/" + name;
        try (InputStream in = ctx.getAssets().open(assetPath)) {
            ByteArrayOutputStream buf = new ByteArrayOutputStream();
            byte[] chunk = new byte[8192];
            int n;
            while ((n = in.read(chunk)) > 0) buf.write(chunk, 0, n);
            return MiniHttpServer.Response.bytes(200, mimeFor(name), buf.toByteArray());
        } catch (Exception e) {
            // 找不到就当 404。前端在 APK 里加载的是 Capacitor 自己的资源服务,
            // 这一条路径主要是给局域网里别的设备看的。
            // 404 when absent. The app's own WebView loads from Capacitor's asset
            // server; this path mainly serves other devices on the LAN.
            return MiniHttpServer.Response.text(404, "not found: " + name);
        }
    }

    private static String mimeFor(String name) {
        String n = name.toLowerCase(Locale.US);
        if (n.endsWith(".html")) return "text/html; charset=utf-8";
        if (n.endsWith(".js")) return "application/javascript; charset=utf-8";
        if (n.endsWith(".css")) return "text/css; charset=utf-8";
        if (n.endsWith(".json")) return "application/json; charset=utf-8";
        if (n.endsWith(".otf")) return "font/otf";
        if (n.endsWith(".ttf")) return "font/ttf";
        if (n.endsWith(".png")) return "image/png";
        if (n.endsWith(".svg")) return "image/svg+xml";
        if (n.endsWith(".tcl")) return "application/x-tcl";
        if (n.endsWith(".txt")) return "text/plain; charset=utf-8";
        return "application/octet-stream";
    }

    // ========================================================================
    // 上传 / upload
    // ========================================================================

    private MiniHttpServer.Response handleUpload(MiniHttpServer.Request req) {
        if (!"POST".equals(req.method)) {
            return MiniHttpServer.Response.json(405, "{\"status\":\"error\",\"message\":\"POST required\"}");
        }
        if (req.body.length == 0) {
            return MiniHttpServer.Response.json(400, "{\"status\":\"error\",\"message\":\"empty body\"}");
        }

        String machineId = req.param("machine_id", "UNKNOWN");
        byte[] payload = req.body;

        // DE1 插件可能用 multipart 上传(和桌面端一样),这里也支持一下
        // The DE1 plugin may upload multipart, as it does against the desktop server
        String ctype = req.headers.get("content-type");
        if (ctype != null && ctype.toLowerCase(Locale.US).contains("multipart/form-data")) {
            byte[] extracted = extractMultipartJson(payload, ctype);
            if (extracted != null) payload = extracted;
        }

        try {
            JSONObject meta = store.save(payload, machineId);

            // save() 里已经把这条挂进了待打印队列,所以现在就叫醒前端 —— 这正是
            // 「App 在后台也能打印」的那一下。前台时它只是让打印快了几秒。
            //
            // save() has already queued this one, so poke the front end now. This is
            // precisely what makes printing work while the app is in the background;
            // in the foreground it merely makes printing a few seconds quicker.
            wakeFrontEnd();

            JSONObject out = new JSONObject();
            out.put("status", "success");
            out.put("id", meta.optString("timestamp"));
            out.put("message", "Shot data received and saved as " + meta.optString("filename"));
            out.put("auto_printed", false);   // 由前端取走队列后打印 / the front end drains the queue and prints
            out.put("filename", meta.optString("filename"));
            return MiniHttpServer.Response.json(200, out.toString());
        } catch (Exception e) {
            Log.w(TAG, "上传解析失败 / upload parse failed: " + e.getMessage());
            JSONObject out = new JSONObject();
            try {
                out.put("status", "error");
                out.put("message", "不是合法的 JSON / not valid JSON: " + e.getMessage());
            } catch (Exception ignored) { }
            return MiniHttpServer.Response.json(400, out.toString());
        }
    }

    /** 从 multipart 里取出第一个文件字段的内容,和 Python 端同样的极简做法。 */
    private static byte[] extractMultipartJson(byte[] body, String contentType) {
        String boundary = MiniHttpServer.parseHeaderParams(contentType).get("boundary");
        if (boundary == null) return null;
        String text = new String(body, StandardCharsets.ISO_8859_1);
        String marker = "--" + boundary;
        int start = text.indexOf(marker);
        while (start >= 0) {
            int headEnd = text.indexOf("\r\n\r\n", start);
            if (headEnd < 0) return null;
            String head = text.substring(start, headEnd);
            if (head.contains("filename=")) {
                int end = text.indexOf(marker, headEnd);
                if (end < 0) end = text.length();
                String content = text.substring(headEnd + 4, end).trim();
                return content.getBytes(StandardCharsets.ISO_8859_1);
            }
            start = text.indexOf(marker, headEnd);
        }
        return null;
    }

    // ========================================================================
    // API
    // ========================================================================

    private MiniHttpServer.Response handleApi(MiniHttpServer.Request req, String path) throws Exception {
        switch (path) {
            case "/api/status":
                return json(status());

            case "/api/shots":
                return json(store.list(req.param("date", "").replace("-", "")).toString());

            case "/api/shot": {
                String file = req.param("file", "");
                File f = store.fileFor(file);
                if (f == null || !f.exists()) {
                    return MiniHttpServer.Response.json(404,
                            "{\"success\":false,\"message\":\"文件不存在 / not found\"}");
                }
                // 原样返回上传的 JSON。展示层翻译(豆子名/方案名)在桌面端由服务端做,
                // 这里没有那套缓存,直接返回原文 —— 前端会按界面语言再兜一层。
                //
                // Returns the uploaded JSON unchanged. Display-layer translation of bean
                // and profile names lives on the desktop server along with its cache;
                // here the raw text goes back and the front end layers its own fallback.
                JSONObject out = new JSONObject();
                out.put("success", true);
                out.put("filename", f.getName());
                out.put("lang", req.param("lang", "zh"));
                out.put("shot", new JSONObject(new String(readFile(f), StandardCharsets.UTF_8)));
                return json(out.toString());
            }

            case "/api/stats":
                return json(store.stats().toString());

            case "/api/queue":
                if ("DELETE".equals(req.method)) {
                    store.clearQueue();
                    return json("{\"success\":true}");
                }
                return json(queueJson().toString());

            case "/api/print-queue": {
                JSONObject out = new JSONObject();
                out.put("jobs", store.pendingJobs());
                return json(out.toString());
            }

            case "/api/print-queue/ack": {
                JSONObject in = new JSONObject(req.bodyText().isEmpty() ? "{}" : req.bodyText());
                String file = new File(in.optString("filename")).getName();
                boolean removed = store.ack(file, in.optBoolean("ok", true));
                JSONObject out = new JSONObject();
                out.put("success", true);
                out.put("removed", removed);
                out.put("jobs", store.pendingJobs());
                return json(out.toString());
            }

            case "/api/printers":
                return json(printersJson().toString());

            case "/api/print/config":
                // Android 上没有纸张/驱动那些选项,只暴露真正相关的几项
                // Android has none of the paper/driver options; only what matters here
                if ("POST".equals(req.method)) {
                    JSONObject in = new JSONObject(req.bodyText().isEmpty() ? "{}" : req.bodyText());
                    if (in.has("printer")) {
                        BluetoothPrinter.get().setDefaultAddress(in.optString("printer"));
                    }
                    JSONObject out = new JSONObject();
                    out.put("success", true);
                    out.put("config", printConfig());
                    return json(out.toString());
                }
                return json(printConfig().toString());

            case "/api/print":
                return handlePrint(req);

            // ---- 桌面端才有、这里明确返回空结果的功能 ----
            // ---- desktop-only features: return a clear empty result ----
            case "/api/settings": {
                JSONObject out = new JSONObject();
                out.put("bean_info_enabled", true);
                out.put("print_enabled", true);
                out.put("max_users", 5);
                return json(out.toString());
            }
            case "/api/language": {
                JSONObject out = new JSONObject();
                out.put("language", "zh");
                return json(out.toString());
            }
            case "/api/languages": {
                JSONArray arr = new JSONArray();
                JSONObject en = new JSONObject(); en.put("code", "en"); en.put("name", "English"); en.put("builtin", true);
                JSONObject zh = new JSONObject(); zh.put("code", "zh"); zh.put("name", "中文"); zh.put("builtin", true);
                arr.put(en); arr.put(zh);
                JSONObject out = new JSONObject();
                out.put("languages", arr);
                return json(out.toString());
            }
            case "/api/settings/ai": {
                JSONObject out = new JSONObject();
                out.put("key_set", false);
                out.put("ai_enabled", false);
                out.put("available", false);
                out.put("message", "Android 版不提供 AI 翻译 / AI translation is not available on Android");
                return json(out.toString());
            }
            case "/api/ai/balance":
                return json("{\"success\":false,\"message\":\"Android 版不提供 AI 翻译\"}");

            default:
                // 未知的 /api/* 返回 JSON 而不是 HTML,前端解 JSON 时才不会炸
                // Unknown /api/* returns JSON, not HTML, so the front end's JSON parse
                // does not blow up.
                return MiniHttpServer.Response.json(404,
                        "{\"success\":false,\"message\":\"此端未实现 / not implemented on this build\"}");
        }
    }

    // ---------------------------------------------------------------- 具体响应
    // ---------------------------------------------------------------- individual responses

    private JSONObject status() throws Exception {
        BluetoothPrinter bp = BluetoothPrinter.get();
        JSONObject out = new JSONObject();
        out.put("status", "Running");
        out.put("version", version);
        out.put("platform", "android");
        out.put("shots_received", store.shotCount());
        // 前端的「启动时间 / 并发用户 / 最大用户」三格直接读这几个字段,不给就显示
        // undefined。桌面端有真实数据,这边给等价的:进程启动时刻、当前线程数、上限。
        //
        // The UI's start-time / active-users / max-users tiles read these fields
        // directly and show "undefined" without them. The desktop side has real
        // values; here the equivalents are the process start time, the live thread
        // count and a ceiling.
        out.put("start_time", _startedAt);
        out.put("active_users", Thread.activeCount());
        out.put("max_users", 5);
        out.put("print_enabled", true);
        out.put("bean_info_enabled", true);
        out.put("language", "zh");
        // 前端据此决定要不要显示「停止服务」按钮 —— Android 上不该显示:
        // 平板上没有终端,但关掉这个服务等于关掉整个 App 的功能,不该给一个一键自杀的入口。
        //
        // The front end uses this to decide whether to offer a stop button. It must be
        // false on Android: there is no terminal on a tablet, but stopping the service
        // also disables the whole app, and a one-tap self-destruct is not worth having.
        out.put("show_stop_button", false);
        out.put("connected_printer", bp == null ? "" : bp.getConnectedAddress());
        out.put("default_printer", bp == null ? "" : bp.getDefaultAddress());
        // 设备自己的局域网地址 —— 用户要把它填进 DE1 插件
        // The device's own LAN address, which the user types into the DE1 plugin
        out.put("lan_ip", DeviceInfo.lanIp());
        out.put("lan_url", DeviceInfo.lanUrl(8000));
        // 后台常驻是否在跑。前端据此显示状态并提供开关 —— 这个值必须来自服务的
        // 真实状态,而不是前端自己的记性:服务可能被系统停掉(用户关掉通知、
        // 厂商省电策略),那时界面还显示「运行中」就是在骗人。
        //
        // Whether the background keep-alive is up. The front end shows it and offers a
        // switch from this value, and it must come from the service's real state rather
        // than the front end's own memory: the system can stop the service — the user
        // dismisses the notification, a vendor battery policy kicks in — and a UI still
        // claiming "running" would be lying.
        out.put("keepalive", PrinterService.isRunning());
        return out;
    }

    private JSONObject printConfig() throws Exception {
        JSONObject out = new JSONObject();
        BluetoothPrinter bp = BluetoothPrinter.get();
        out.put("platform", "android");
        out.put("platform_name", "Android (Bluetooth ESC/POS)");
        out.put("available", true);
        out.put("printer", bp == null ? "" : bp.getDefaultAddress());
        out.put("mode", "");
        out.put("print_width", 576);
        out.put("paper_width_mm", 80);
        out.put("feed_lines", 3);
        out.put("cut", true);
        out.put("threshold", 200);
        out.put("rotate", true);
        out.put("raw_supported", true);
        return out;
    }

    private JSONObject printersJson() throws Exception {
        JSONObject out = new JSONObject();
        JSONArray arr = new JSONArray();
        BluetoothPrinter bp = BluetoothPrinter.get();
        if (bp != null) {
            try {
                List<BluetoothPrinter.PrinterEntry> entries = bp.listPrinters();
                for (BluetoothPrinter.PrinterEntry e : entries) {
                    JSONObject o = new JSONObject();
                    o.put("id", e.address);
                    o.put("name", e.displayName());
                    o.put("address", e.address);
                    o.put("paired", e.paired);
                    o.put("default", e.isDefault);
                    arr.put(o);
                }
            } catch (Exception e) {
                Log.w(TAG, "列出打印机失败 / listPrinters failed: " + e.getMessage());
            }
        }
        out.put("platform", "android");
        out.put("printers", arr);
        out.put("available", true);
        return out;
    }

    private JSONObject queueJson() throws Exception {
        JSONObject out = new JSONObject();
        out.put("count", store.pendingJobs().length());
        out.put("jobs", new JSONArray());
        return out;
    }

    /**
     * 通过蓝牙打印 / print over Bluetooth.
     *
     * **接收的是完整的 ESC/POS 字节流,不是位图。** 客户端自己渲染、自己拼指令,
     * 这里只负责写进蓝牙 socket。
     *
     * 为什么不在这里用 bitmap 拼 `GS v 0` 指令:
     *
     *   ESC/POS 的拼装**只有一份实现**,在 `web/printer.js` 里(与
     *   `printers/escpos.py` 逐字节对应)。设备上的 Capacitor 插件走的也是那条路。
     *   在这里再实现一遍,就等于开第二个可能走偏的地方,而症状会是「通过局域网打出来的
     *   和本机打印出来的不一样」—— 恰好是最难联想到「协议实现分叉」的那种现象。
     *
     *   所以这一端和原生插件一样,是个纯粹的字节管道。
     *
     * Takes a complete ESC/POS byte stream, not a bitmap. The client renders and
     * assembles; this end only writes to the Bluetooth socket.
     *
     * Why no bitmap-to-command assembly here: the ESC/POS assembly has exactly one
     * implementation, in web/printer.js (byte-for-byte matching printers/escpos.py),
     * which the on-device Capacitor plugin also goes through. A second one here would
     * be another place to drift, and the symptom would be "printing over the LAN
     * differs from printing on the device" — exactly the kind of thing nobody traces
     * back to a forked protocol implementation.
     *
     * So this end is a plain byte pipe, same as the native plugin.
     */
    private MiniHttpServer.Response handlePrint(MiniHttpServer.Request req) throws Exception {
        if (!"POST".equals(req.method)) {
            return MiniHttpServer.Response.json(405, "{\"success\":false,\"message\":\"POST required\"}");
        }
        JSONObject in = new JSONObject(req.bodyText().isEmpty() ? "{}" : req.bodyText());
        String b64 = in.optString("data", "");
        if (b64.isEmpty()) {
            JSONObject out = new JSONObject();
            out.put("success", false);
            out.put("message", "缺少 data 字段 —— 本端接收完整的 ESC/POS 字节流(base64),"
                    + "不接受 bitmap;请在客户端用 printer.js 的 buildJob() 拼好再发过来。"
                    + " missing 'data': this end takes a complete ESC/POS job (base64), "
                    + "not a bitmap; assemble it client-side with printer.js buildJob().");
            return MiniHttpServer.Response.json(400, out.toString());
        }

        byte[] job;
        try {
            job = android.util.Base64.decode(b64, android.util.Base64.DEFAULT);
        } catch (Exception e) {
            return MiniHttpServer.Response.json(400,
                    "{\"success\":false,\"message\":\"base64 解码失败 / bad base64\"}");
        }
        if (job.length == 0) {
            return MiniHttpServer.Response.json(400,
                    "{\"success\":false,\"message\":\"空的打印任务 / empty job\"}");
        }

        String address = in.optString("printer", "");
        if ("default".equals(address)) address = "";
        BluetoothPrinter bp = BluetoothPrinter.get();
        boolean ok = bp != null && bp.printJob(job, address.isEmpty() ? null : address);

        JSONObject out = new JSONObject();
        out.put("success", ok);
        out.put("message", ok ? "打印任务已发送 / print job sent"
                : ("打印失败 / print failed: " + (bp == null ? "not ready" : bp.getLastError())));
        out.put("platform", "android");
        out.put("bytes", job.length);
        return MiniHttpServer.Response.json(200, out.toString());
    }

    // ---------------------------------------------------------------- 工具
    // ---------------------------------------------------------------- helpers

    /**
     * 200 + JSON。接受 Object 而不是 String:调用点传的多半是 JSONObject/JSONArray,
     * 让每个调用点自己 .toString() 只会到处重复,还容易漏。
     *
     * 200 + JSON. Takes an Object rather than a String: call sites almost always have a
     * JSONObject or JSONArray, and making each one stringify itself just repeats code
     * and invites a forgotten call.
     */
    private static MiniHttpServer.Response json(Object body) {
        return MiniHttpServer.Response.json(200, String.valueOf(body));
    }

    private static byte[] readFile(File f) throws Exception {
        try (FileInputStream in = new FileInputStream(f)) {
            ByteArrayOutputStream buf = new ByteArrayOutputStream();
            byte[] chunk = new byte[8192];
            int n;
            while ((n = in.read(chunk)) > 0) buf.write(chunk, 0, n);
            return buf.toByteArray();
        }
    }
}
