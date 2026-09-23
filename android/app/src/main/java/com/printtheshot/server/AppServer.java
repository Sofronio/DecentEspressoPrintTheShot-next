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
import java.io.FileOutputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.Arrays;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;

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

    // 备份包的上限,和 Python 端保持一致。按**实际解压出的字节数**设限,而不是信
    // zip 头里声明的 size —— 声明值可以撒谎(这正是 zip 炸弹的构造方式),读出来的
    // 不会。
    //
    // Backup caps, matching the Python side. They apply to the bytes actually inflated,
    // not to the size declared in the zip header: the header can lie (that is exactly how
    // a zip bomb is built), the bytes cannot.
    private static final int MAX_BACKUP_ENTRY = 8 * 1024 * 1024;
    private static final long MAX_BACKUP_UNPACKED = 256L * 1024 * 1024;
    private static final byte[] CRLFCRLF = {'\r', '\n', '\r', '\n'};

    /**
     * GitHub 的 release **列表**(不是 /releases/latest)。
     *
     * 为什么不用 latest:GitHub 对它的定义是「最新的**非 pre-release、非 draft**
     * 的 release」,一个 pre-release 都没有时直接 **404**。而本项目在 beta 阶段
     * 每个 release 都标 pre-release,用 latest 会让检查更新整个失效 —— 而且失效
     * 得隐蔽:404 被走成「查询失败」,界面显示 ❌ 而不是「有新版」。
     *
     * 自己从列表挑最大还顺带把排序变成**按版本号**而不是按发布日期。
     *
     * The release **list**, not /releases/latest.
     *
     * Why not latest: GitHub defines it as "the most recent **non-prerelease,
     * non-draft** release" and answers **404** when there is none. This project flags
     * every release as a pre-release during the beta phase, so latest would break
     * update checking — and quietly: a 404 becomes the "lookup failed" path, showing a
     * ❌ instead of "there is a new version".
     *
     * Picking the maximum ourselves also makes the ordering **by version** rather than
     * by publish date.
     */
    private static final String GITHUB_API_RELEASES =
            "https://api.github.com/repos/Sofronio/DecentEspressoPrintTheShot-next/releases?per_page=30";

    /**
     * 查不到 latest 时的退路:整个 releases 列表页。
     *
     * 比没有链接强 —— 检查失败的时候用户至少还能自己去看一眼,而不是对着一句
     * 「检查失败」束手无策。
     *
     * The fallback when the latest release cannot be fetched: the releases list page.
     *
     * Better than no link at all — when the check fails the user can still go look,
     * instead of being left with a dead end.
     */
    private static final String GITHUB_RELEASES_PAGE =
            "https://github.com/Sofronio/DecentEspressoPrintTheShot-next/releases";

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

        // ---- DE1 插件下载 ----
        if (p.startsWith("/plugin/")) return servePlugin(p);

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
    /**
     * 提供 DE1 插件下载 / serve the DE1 plugin downloads.
     *
     * 为什么不能直接走 serveAsset(通用资源分发)
     * ----------------------------------------
     * 因为那样**没有 Content-Disposition**,而在这个场景里它是关键的一行:
     *
     *   - `.tcl` 在 WebView 里渲染不了,所以哪怕没有它也会触发下载事件;
     *   - `.txt` **能**渲染 —— WebView 会把它当文本显示出来。用户看到的是「点了
     *     能打开,但没存下来」,而那不是"下载失败",是压根没走下载。
     *
     * Python 服务端一直是带 attachment 发的(所以桌面浏览器会存),Android 这一侧
     * 漏了,于是同一个按钮在两个平台上行为不同。这里补齐,两端一致。
     *
     * Why this cannot just fall through to serveAsset
     * ----------------------------------------------
     * Because that sets no **Content-Disposition**, and here it is the load-bearing
     * header:
     *
     *   - `.tcl` cannot be rendered by a WebView, so it raises a download event anyway;
     *   - `.txt` **can** — the WebView displays it as text. What the user sees is "it
     *     opens but is not saved", which is not a failed download: it never was one.
     *
     * The Python server has always sent these as attachments (which is why a desktop
     * browser saves them); the Android side was missing it, so the same button behaved
     * differently on the two platforms. This makes them agree.
     */
    private MiniHttpServer.Response servePlugin(String path) {
        String name = path.substring("/plugin/".length());
        boolean isTxt = name.equals("plugin.tcl.txt");
        if (!isTxt && !name.equals("plugin.tcl")) {
            return MiniHttpServer.Response.text(404, "not found: plugin/" + name);
        }

        MiniHttpServer.Response r = serveAsset(path);
        if (r.status == 200) {
            // TXT 版存下来的文件名就是 tcl.txt —— 它存在的理由就是蓝牙发送时
            // 安卓端拒收 .tcl,换个扩展名就能过。
            // The TXT copy saves as tcl.txt: it exists because Android refuses .tcl over
            // Bluetooth, and a different extension gets through.
            String download = isTxt ? "tcl.txt" : "plugin.tcl";
            r.extraHeaders.put("Content-Disposition",
                    "attachment; filename=\"" + download + "\"");
        }
        return r;
    }

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

            // ---- 更新 ----
            // ---- updates ----
            //
            // 这两个路径从前落进下面的 default,回一句笼统的「此端未实现」。前端拿到
            // 一个没有 update_available 的 JSON,把「缺席」读成 falsy,于是一路走到
            // 绿色的「已是最新」—— 没检查,却给了结论。
            //
            // 现在 /check 是**真的去查**:平板自己不能在线更新(它是个 APK),但
            // 「有没有新版」这件事照样问得出来 —— 查 GitHub 上最新的 release,和本机
            // 版本比一下。查到有新版,前端把按钮变成「下载新 APK」并指向那个 release
            // 页面。
            //
            // /api/update(POST,原地更新)在本版确实没有 —— 那条路要能把 APK 换成
            // 自己,而 Android 不给应用这个权力。前端在这端也不再 POST 它,但仍然
            // 如实回答,而不是假装成功。
            //
            // These two used to fall through to the default and answer with a generic
            // "not implemented on this build". The front end got JSON with no
            // update_available, read the absence as falsy, and landed on a green "up to
            // date" — a verdict with no check behind it.
            //
            // /check now really checks. The tablet cannot update itself (it is an APK),
            // but "is there a newer version" is still answerable — ask GitHub for the
            // latest release and compare it with this build. When there is one, the front
            // end turns the button into "download the APK" and points it at that release
            // page.
            //
            // /api/update (POST, in-place) genuinely does not exist on this build: that
            // path would have to replace the APK with itself, which Android does not let
            // an app do. The front end no longer POSTs it here, but it still answers
            // honestly rather than pretending to succeed.
            case "/api/update/check":
                return checkUpdate(req);

            case "/api/update": {
                JSONObject out = new JSONObject();
                out.put("success", false);
                out.put("unsupported", true);
                out.put("message", "本版通过安装新 APK 更新,不支持在线更新 / "
                        + "This build updates by installing a new APK; online update is not available");
                return json(out.toString());
            }

            // ---- 备份与恢复 / backup & restore ----
            case "/api/backup/export":
                return exportBackup();

            case "/api/backup/import":
                return importBackup(req);

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
        // 第二个更新按钮该干什么 —— 界面一加载就要知道(见 /api/update/check 的说明)。
        // "apk":本版通过安装新 APK 更新,按钮是「下载新 APK」,打开 release 页面。
        //
        // What the second update button should do — the UI has to know this as soon as
        // it loads (see the note on /api/update/check). "apk": this build updates by
        // installing a new APK, so the button is "download the APK" and opens the
        // release page.
        out.put("update_via", "apk");
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

    /**
     * 从 multipart 里取出第一个文件字段的**原始字节**,和 Python 端的
     * _extract_multipart_bytes 是同一套做法。
     *
     * 和 extractMultipartJson 干的是同一件事,区别只在于全程不解码 —— 备份包是
     * zip(二进制),先 new String(...) 会把内容毁掉。
     *
     * The minimal multipart parse, pulling the raw bytes of the first file field. Same
     * job as extractMultipartJson except nothing is ever decoded — a backup is a zip
     * (binary), and turning it into a String first would corrupt it.
     */
    private static byte[] extractMultipartBytes(byte[] body, String contentType) {
        String boundary = MiniHttpServer.parseHeaderParams(contentType).get("boundary");
        if (boundary == null) return null;
        byte[] marker = ("--" + boundary).getBytes(StandardCharsets.ISO_8859_1);
        int start = indexOf(body, marker, 0);
        while (start >= 0) {
            int headStart = start + marker.length;
            int headEnd = indexOf(body, CRLFCRLF, headStart);
            if (headEnd < 0) return null;
            String head = new String(body, headStart, headEnd - headStart,
                    StandardCharsets.ISO_8859_1);
            if (head.contains("filename=")) {
                int contentStart = headEnd + 4;
                int next = indexOf(body, marker, contentStart);
                int contentEnd = (next < 0) ? body.length : next;
                // boundary 前那个 CRLF 是分隔符,不属于内容 —— 精确去掉两个字节。
                // 不能像 JSON 版那样 trim/rstrip:二进制内容末尾真的以 CRLF 结束时
                // 会被误删。
                //
                // The CRLF before the boundary separates it from the content and is not
                // part of it — drop exactly those two bytes. Trimming, as the JSON
                // version does, would eat real content that genuinely ends in CRLF.
                if (contentEnd - contentStart >= 2
                        && body[contentEnd - 2] == '\r' && body[contentEnd - 1] == '\n') {
                    contentEnd -= 2;
                }
                return Arrays.copyOfRange(body, contentStart, contentEnd);
            }
            start = indexOf(body, marker, headStart);
        }
        return null;
    }

    /** byte[] 的 indexOf —— Java 没有现成的 / byte[] indexOf; Java has no built-in. */
    private static int indexOf(byte[] haystack, byte[] needle, int from) {
        outer:
        for (int i = Math.max(0, from); i <= haystack.length - needle.length; i++) {
            for (int j = 0; j < needle.length; j++) {
                if (haystack[i + j] != needle[j]) continue outer;
            }
            return i;
        }
        return -1;
    }

    // ---------------------------------------------------------------- 更新
    // ---------------------------------------------------------------- updates

    /**
     * GET /api/update/check —— 查 GitHub 上最新的 release,和本机版本比。
     *
     * 数据源是 release **列表**,自己在里面挑最大的那个 —— 为什么不用
     * /releases/latest 见 GITHUB_API_RELEASES 上面那段(一句话:它排除
     * pre-release,而本项目 beta 阶段每个 release 都是 pre-release)。
     *
     * The source is the release **list**, picking the maximum ourselves — for why not
     * /releases/latest see the note above GITHUB_API_RELEASES (in one line: it excludes
     * pre-releases, and every release this project cuts during the beta phase is one).
     *
     * 返回 / returns:
     *   `{success:true, local, remote, update_available, release_url, update_via}`
     *   失败时 `{success:false, error, release_url}`,前端走 ❌ 分支 —— 查不到就
     *   说查不到,绝不给一个绿色的结论。
     *
     *   On failure: `{success:false, error, release_url}` and the front end takes its
     *   ❌ branch. A failed check says so; it never produces a green verdict.
     */
    private MiniHttpServer.Response checkUpdate(MiniHttpServer.Request req) throws Exception {
        JSONObject out = new JSONObject();
        java.net.HttpURLConnection conn = null;
        String channel = req.param("channel", "auto");
        if (!"stable".equals(channel) && !"beta".equals(channel)) channel = "auto";
        try {
            conn = (java.net.HttpURLConnection) new java.net.URL(GITHUB_API_RELEASES).openConnection();
            conn.setConnectTimeout(12000);
            conn.setReadTimeout(12000);
            // GitHub API 不带 User-Agent 会直接 403,不是可选项
            // The GitHub API returns 403 without a User-Agent; it is not optional.
            conn.setRequestProperty("User-Agent", "PrintTheShotNext/" + version);
            conn.setRequestProperty("Accept", "application/vnd.github+json");

            int code = conn.getResponseCode();
            if (code != 200) {
                Log.w(TAG, "查 release 列表失败 / release-list lookup failed: HTTP " + code);
                out.put("success", false);
                out.put("error", "GitHub 返回 " + code + " / GitHub returned " + code);
                out.put("release_url", GITHUB_RELEASES_PAGE);
                return json(out.toString());
            }

            // 自己在列表里挑最大的那个 —— 理由见 GITHUB_API_RELEASES 上面那段。
            // 顺带一条:已经在用正式版的人不该被推去装 beta,所以本地是正式版时
            // 只在正式版里挑;本地还在 beta 时两者一起挑(它得能升到正式版)。
            //
            // Pick the maximum ourselves — see the note above GITHUB_API_RELEASES.
            // One extra rule: someone already on a final release should not be pushed
            // onto a beta, so a final local version only considers final releases while
            // a beta local version considers both (it has to be able to move up).
            // 频道决定要不要把 beta 算进来:
            //   stable —— 只在正式版里挑,用稳定版的人不该被推去装 beta
            //   beta   —— 正式版和 beta 一起挑
            //   auto   —— 频道跟着本机版本走(界面不传这个,是给直接调接口的默认)
            //
            // 三分支,不能压成一行。这里踩过:原本写成
            // `"beta".equals(channel) || isPrerelease(version)`,那个 `||` 把 auto 的
            // 规则漏给了所有频道 —— 于是在一台 beta 版机器上,显式的 channel=stable
            // 被静默忽略,beta 又被算了进来,「稳定版频道」查出了 beta。实测于平板上。
            //
            // Three separate branches; do not collapse them. This was hit: it read
            // `"beta".equals(channel) || isPrerelease(version)`, and that `||` leaked the
            // auto rule into every channel — so on a beta build an explicit
            // channel=stable was silently ignored and betas counted again, making the
            // "stable" channel return a beta. Measured on the tablet.
            //
            // The channel decides whether betas count:
            //   stable — finals only; someone on a stable build should not be pushed onto
            //            a beta
            //   beta   — finals and betas together
            //   auto   — follow the local version (the UI never sends this; it is the
            //            default for anyone calling the endpoint directly)
            boolean wantPrerelease;
            if ("beta".equals(channel)) {
                wantPrerelease = true;
            } else if ("stable".equals(channel)) {
                wantPrerelease = false;
            } else {
                wantPrerelease = isPrerelease(version);
            }

            JSONArray releases = new JSONArray(readStream(conn.getInputStream()));
            String tag = "", url = GITHUB_RELEASES_PAGE;
            for (int i = 0; i < releases.length(); i++) {
                JSONObject rel = releases.optJSONObject(i);
                if (rel == null || rel.optBoolean("draft", false)) continue;
                if (rel.optBoolean("prerelease", false) && !wantPrerelease) continue;
                String t = rel.optString("tag_name", "");
                if (t.isEmpty()) continue;
                if (tag.isEmpty() || versionCompare(t, tag) > 0) {
                    tag = t;
                    url = rel.optString("html_url", GITHUB_RELEASES_PAGE);
                }
            }
            // 频道为空不算错误 —— 说清楚它,而不是报一个「查不到」,更不是「已是最新」
            // An empty channel is not an error: say so, rather than reporting a failed
            // lookup — and certainly not "up to date".
            if (tag.isEmpty()) {
                out.put("success", true);
                out.put("local", version);
                out.put("remote", "");
                out.put("channel", channel);
                out.put("update_available", false);
                out.put("release_url", GITHUB_RELEASES_PAGE);
                out.put("update_via", "apk");
                out.put("message", "这个频道还没有发布过版本 / Nothing released in this channel yet");
                return json(out.toString());
            }
            boolean newer = versionCompare(tag, version) > 0;

            // 日志里把两个版本和结论都写出来 —— 更新这件事出问题时,「它到底以为
            // 自己是什么版本」永远是要问的第一个问题。
            //
            // Log both versions and the verdict. When updates misbehave, "what version
            // does it think it is" is always the first question.
            Log.i(TAG, "检查更新 / update check: 本机 " + version + " vs 远端 " + tag
                    + " -> " + (newer ? "有新版 / newer" : "已是最新 / up to date"));

            out.put("success", true);
            out.put("local", version);
            out.put("remote", tag);
            out.put("channel", channel);
            out.put("update_available", newer);
            out.put("release_url", url);
            out.put("update_via", "apk");
            return json(out.toString());
        } catch (Exception e) {
            Log.w(TAG, "检查更新失败 / update check failed: " + e.getMessage());
            out.put("success", false);
            out.put("error", String.valueOf(e.getMessage()));
            out.put("release_url", GITHUB_RELEASES_PAGE);
            return MiniHttpServer.Response.json(500, out.toString());
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    /**
     * 把版本号解析成可比较的数组,与 Python 端的 `_version_key` **逐条对应** ——
     * 两端对「谁更新」必须给出同一个答案,否则同一台机器在桌面和 App 上会看到
     * 相反的结论。改动其中一处时,另一处要跟着改,测试用例也是同一组。
     *
     *     '1.0.2'       -> (1, 0, 2, 1, 0)     正式版,哨兵 1
     *     '1.0.3'       -> (1, 0, 3, 1, 0)     比上面大
     *     'v2.1-beta.3' -> (1, 0, 2, 0, 3)     旧编号映射进新标尺,预发布哨兵 0
     *
     * Parses a version into a comparable array, mirroring the Python `_version_key`
     * **item for item**: the two must agree on "which is newer", or the same machine
     * would see opposite answers on the desktop and in the app. Change one, change the
     * other; the test cases are the same list.
     */
    private static int[] versionKey(String v) {
        String s = v == null ? "" : v.trim();
        // release tag 带 v 前缀(v1.0.3),而下面的正则从头匹配 —— 不剥掉的话解析
        // 失败、归到全零,远端就永远显得比本地旧,界面永远说「已是最新」。
        //
        // Release tags carry a leading v (v1.0.3) and the pattern below anchors at the
        // start: without stripping it the parse fails and lands on all zeros, making
        // the remote look older than anything local — "up to date" forever.
        while (s.startsWith("v") || s.startsWith("V")) s = s.substring(1);

        java.util.regex.Matcher m = java.util.regex.Pattern
                .compile("^(\\d+)\\.(\\d+)(?:\\.(\\d+))?").matcher(s);
        if (!m.find()) return new int[]{0, 0, 0, 0, 0};
        int major = Integer.parseInt(m.group(1));
        int minor = Integer.parseInt(m.group(2));
        int patch = m.group(3) != null ? Integer.parseInt(m.group(3)) : 0;

        java.util.regex.Matcher pre = java.util.regex.Pattern
                .compile("(alpha|beta|rc|next|pre)[.\\-]?(\\d+)",
                        java.util.regex.Pattern.CASE_INSENSITIVE).matcher(s);
        if (pre.find()) {
            int n = Integer.parseInt(pre.group(2));
            // 旧编号时代(2.1-beta.N)映射成 1.0.0-beta.N —— 也就是那些 release
            // 改名之后的名字,一一对应。
            //
            // 不映射的话,还装着 2.1-beta.3 的机器会拿 2.1 和 1.0 比,得出「我更新」,
            // 从此收不到更新 —— 那正是「谎报已是最新」的同门错误。
            //
            // Versions from the old numbering (2.1-beta.N) map to 1.0.0-beta.N — exactly
            // the names those releases were renamed to, one for one.
            //
            // Without it, a machine on 2.1-beta.3 compares 2.1 against 1.0, concludes it
            // is ahead, and never sees another update — the same family of error as
            // reporting "up to date" without checking.
            if (major == 2) return new int[]{1, 0, 0, 0, n};
            return new int[]{major, minor, patch, 0, n};
        }
        return new int[]{major, minor, patch, 1, 0};
    }

    /** 按 versionKey 比较两个版本 / compare two versions the way versionKey defines. */
    private static int versionCompare(String a, String b) {
        int[] x = versionKey(a), y = versionKey(b);
        for (int i = 0; i < x.length; i++) {
            if (x[i] != y[i]) return Integer.compare(x[i], y[i]);
        }
        return 0;
    }

    /** 是不是预发布版(哨兵位为 0)/ whether a version is a prerelease (sentinel 0). */
    private static boolean isPrerelease(String v) {
        return versionKey(v)[3] == 0;
    }

    /** 读一个流到字符串 / read a stream into a string. */
    private static String readStream(InputStream in) throws Exception {
        try (InputStream src = in) {
            ByteArrayOutputStream buf = new ByteArrayOutputStream();
            byte[] chunk = new byte[8192];
            int n;
            while ((n = src.read(chunk)) > 0) buf.write(chunk, 0, n);
            return new String(buf.toByteArray(), StandardCharsets.UTF_8);
        }
    }

    // ---------------------------------------------------------------- 备份
    // ---------------------------------------------------------------- backup

    /**
     * GET /api/backup/export —— 把 shot 数据打成一个 zip 返回。
     *
     * 内容与桌面端 Python 的导出保持一致(平铺的 *.json,含 index.json),这样两边
     * 的备份包可以互相导入。
     *
     * Contents match the desktop Python export (flat *.json entries, index.json
     * included), so the archives are interchangeable between the two.
     */
    private MiniHttpServer.Response exportBackup() throws Exception {
        ByteArrayOutputStream buf = new ByteArrayOutputStream();
        int count = 0;
        try (ZipOutputStream z = new ZipOutputStream(buf)) {
            File[] files = store.getDir().listFiles();
            if (files != null) {
                Arrays.sort(files);
                for (File f : files) {
                    if (!f.isFile() || !f.getName().endsWith(".json")) continue;
                    z.putNextEntry(new ZipEntry(f.getName()));
                    z.write(readFile(f));
                    z.closeEntry();
                    count++;
                }
            }
        }
        byte[] payload = buf.toByteArray();
        String ts = new SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(new Date());

        MiniHttpServer.Response r = MiniHttpServer.Response.bytes(200, "application/zip", payload);
        r.extraHeaders.put("Content-Disposition",
                "attachment; filename=\"printtheshot_backup_" + ts + ".zip\"");
        // 备份纪律:打印实际打包的条数和字节数,而不是只说了一句「导出了」。
        // Backup discipline: report the entries and bytes actually packed rather than
        // just announcing that an export happened.
        Log.i(TAG, "已导出备份 / backup exported: " + count + " 个文件,"
                + payload.length + " 字节");
        return r;
    }

    /**
     * POST /api/backup/import —— 收下一个备份 zip,把里面的 shot 恢复回去。
     *
     * 语义是**恢复**,不是合并:同名文件按内容覆盖。这正是「导回」该有的样子 ——
     * 用户拿着备份回来,期望的是回到导出时的样子,而不是新旧混在一起。
     *
     * 三条纪律与 Python 端一致:条目名一律取 basename(`../` 不该落盘)、跳过
     * index.json(生成物,索引会自愈重建)、不触发打印也不入队(恢复历史不是新
     * 数据,不该出纸)。
     *
     * Semantics are **restore**, not merge: a matching filename is overwritten. That is
     * what "import my backup" should mean — the user expects to get back what they
     * exported, not a blend of old and new.
     *
     * The three rules match the Python side: entry names are reduced to their basename (a
     * `../` never reaches the disk), index.json is skipped (derived; the index rebuilds
     * itself by scanning), and nothing is printed or queued (restoring history is not new
     * data).
     */
    private MiniHttpServer.Response importBackup(MiniHttpServer.Request req) throws Exception {
        JSONObject out = new JSONObject();
        if (req.body.length == 0) {
            out.put("success", false);
            out.put("message", "空请求 / empty body");
            return MiniHttpServer.Response.json(400, out.toString());
        }

        byte[] body = req.body;
        String ctype = req.headers.get("content-type");
        if (ctype != null && ctype.toLowerCase(Locale.US).contains("multipart/form-data")) {
            byte[] extracted = extractMultipartBytes(body, ctype);
            if (extracted != null) body = extracted;
        }

        int imported = 0, skipped = 0;
        long unpacked = 0;
        JSONArray skippedNames = new JSONArray();

        // 先落在临时文件上再打开:ZipFile 只能从文件读,但它有两个 ZipInputStream
        // 给不了的东西 ——
        //
        // 1. **明确回答「这是不是 zip」**。ZipInputStream 对垃圾输入不抛异常,只是一
        //    个条目都读不出来 —— 那和「包是空的」长得一模一样,于是「这根本不是备份」
        //    会被报成「成功恢复 0 条」。这正是我在这台设备上实测到的假成功。
        //    ZipFile 的构造函数对非 zip 直接抛 ZipException。
        // 2. **每个条目独立成败**。Android 的 zip 解析自带路径校验(条目名含 `../`
        //    时抛「Invalid zip entry path」),而 ZipInputStream 是在 getNextEntry 里
        //    抛的 —— 那时前面的条目已经写进去了,想只跳过那一个坏条目都做不到,只能
        //    整包中止,于是「导了一半然后报失败」。按条目读就没有这个问题。
        //
        // A temp file first: ZipFile only reads from a file, and it offers two things
        // ZipInputStream cannot.
        //
        // 1. **A definite "is this a zip" answer.** ZipInputStream does not throw on
        //    garbage; it merely yields no entries, which is indistinguishable from an
        //    empty archive — so "this is not a backup at all" got reported as
        //    "successfully restored 0 records". That false success was measured on this
        //    very device. ZipFile's constructor throws ZipException on a non-zip.
        // 2. **Per-entry success and failure.** Android's zip parsing validates entry
        //    paths itself (a `../` name throws "Invalid zip entry path"), and
        //    ZipInputStream throws that from getNextEntry — by which point earlier
        //    entries are already written, so skipping just the one bad entry is
        //    impossible and the whole archive has to be abandoned, reporting a failure
        //    for an import that half happened. Reading entry by entry avoids that.
        File tmp = File.createTempFile("pts_backup", ".zip", ctx.getCacheDir());
        try {
            try (FileOutputStream fos = new FileOutputStream(tmp)) {
                fos.write(body);
            }
            try (java.util.zip.ZipFile zf = new java.util.zip.ZipFile(tmp)) {
                java.util.Enumeration<? extends ZipEntry> entries = zf.entries();
                while (entries.hasMoreElements()) {
                    ZipEntry e = entries.nextElement();
                    if (e.isDirectory()) continue;
                    String entry = e.getName().replace('\\', '/');

                    // 带 ".." 的条目直接跳过,而不是安静地取个 basename 收下 —— 包里
                    // 出现 `../` 只有两种可能:坏了,或者恶意。两种都不该被当作一条
                    // 正常记录导进来,用户也该在「跳过」里看到它。
                    //
                    // Android 的 zip 解析往往更早一步自己抛错,那种情况由下面按条目的
                    // catch 兜住;这一行保证两台服务端的行为一致。
                    //
                    // Entries containing ".." are skipped rather than quietly taken
                    // under their basename: a `../` in an archive is either corrupt or
                    // hostile, and neither should come in as a legitimate record. The
                    // user should see it listed as skipped.
                    //
                    // Android's own zip parsing often throws first, and the per-entry
                    // catch below absorbs that; this line keeps the two servers behaving
                    // the same either way.
                    if (Arrays.asList(entry.split("/")).contains("..")) {
                        skipped++;
                        skippedNames.put(e.getName());
                        continue;
                    }
                    // 仍然只取 basename 落盘:最后一道关口,不指望上面那行。
                    // Still write under the basename: the last gate before the disk, and
                    // it does not rely on the check above.
                    String name = new File(entry).getName();
                    if (!name.endsWith(".json") || name.equals("index.json")) {
                        skipped++;
                        skippedNames.put(e.getName());
                        continue;
                    }

                    try {
                        ByteArrayOutputStream ebuf = new ByteArrayOutputStream();
                        long size = 0;
                        boolean tooBig = false;
                        try (InputStream in = zf.getInputStream(e)) {
                            byte[] chunk = new byte[8192];
                            int n;
                            while ((n = in.read(chunk)) > 0) {
                                size += n;
                                if (size > MAX_BACKUP_ENTRY
                                        || unpacked + size > MAX_BACKUP_UNPACKED) {
                                    tooBig = true;
                                    break;
                                }
                                ebuf.write(chunk, 0, n);
                            }
                        }
                        if (tooBig) {
                            skipped++;
                            skippedNames.put(e.getName());
                            continue;
                        }
                        unpacked += size;
                        byte[] raw = ebuf.toByteArray();

                        // 先解析再落盘:存进一个坏文件只会让以后每次列表都出错。
                        // Parse before writing: a malformed file would break every later
                        // listing.
                        new JSONObject(new String(raw, StandardCharsets.UTF_8));

                        store.importFile(name, raw);
                        imported++;
                    } catch (Exception badEntry) {
                        // 坏条目跳过而不是整体失败:一个坏文件不该让其余几百条白导。
                        // Skip the bad entry rather than failing the whole import: one
                        // malformed file should not cost the other few hundred.
                        Log.w(TAG, "跳过一个坏条目 / skipping a bad entry: "
                                + e.getName() + " — " + badEntry.getMessage());
                        skipped++;
                        skippedNames.put(e.getName());
                    }
                }
            } catch (java.util.zip.ZipException refused) {
                // 两种原因都会落到这里,必须分清 —— 否则消息本身在骗人:
                //   a) 根本不是 zip;
                //   b) 是 zip,但里面有非法路径条目,Android 的解析器直接拒绝整包。
                //
                // b 在 Python 端是「跳过那一条、其余照常导入」,Android 上做不到:平台
                // 的解析器在**构造函数里**就抛了,整包原子拒绝。实测过 —— 好条目放在
                // 前面也一样,一条都不会落盘,所以不存在「导了一半」的中间状态。
                //
                // Two causes land here and they have to be told apart, or the message
                // itself lies: (a) it is not a zip at all, or (b) it is a zip carrying an
                // illegal entry path, which Android's parser refuses wholesale.
                //
                // (b) is "skip that entry, import the rest" on the Python side, which
                // Android cannot do: the platform parser throws in the **constructor**,
                // rejecting the archive atomically. That was measured — good entries
                // placed first land none the same, so there is no half-imported state.
                String detail = String.valueOf(refused.getMessage());
                boolean badEntryPath = detail.contains("Invalid zip entry path");
                Log.w(TAG, "备份包被拒绝 / archive refused: " + detail);
                out.put("success", false);
                out.put("message", badEntryPath
                        ? "备份包里有非法路径条目,已拒绝整个包 / the archive contains an "
                          + "illegal entry path; the whole archive was refused"
                        : "这不是有效的备份包(不是 zip 文件)/ "
                          + "Not a valid backup archive (not a zip file)");
                return MiniHttpServer.Response.json(400, out.toString());
            }
        } finally {
            // 临时文件里是用户的整份数据,用完必须删,别留在缓存目录里。
            // The temp file holds the user's entire dataset; it has to go, not sit in
            // the cache directory.
            if (!tmp.delete()) {
                Log.w(TAG, "临时备份文件没删掉 / temp backup file not deleted: " + tmp);
            }
        }

        // 备份纪律:报出实际落盘的条数,数不对就是没导成,别让它看起来像成了。
        // Backup discipline: report what actually landed; wrong numbers mean it did not
        // work, and nothing should suggest otherwise.
        Log.i(TAG, "已导入备份 / backup imported: " + imported + " 条,跳过 " + skipped + " 条");

        String msg = "已恢复 " + imported + " 条记录";
        if (skipped > 0) msg += ",跳过 " + skipped + " 条";
        out.put("success", true);
        out.put("imported", imported);
        out.put("skipped", skippedNames);
        out.put("message", msg);
        return json(out.toString());
    }
}
