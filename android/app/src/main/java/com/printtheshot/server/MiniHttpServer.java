package com.printtheshot.server;

import android.util.Log;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * 极简 HTTP 服务端 / a minimal HTTP server
 * =========================================
 *
 * 中文
 * ----
 * 在设备上提供 Web UI 与 API,让平板自己成为一个打印节点 —— DE1 直接把 shot JSON
 * 传过来,平板渲染并蓝牙打印,不需要任何电脑参与。
 *
 * 为什么手写而不是用 NanoHTTPD 之类的库:
 *
 *   App 的原生源码是以「叠加」的方式合进 Capacitor 生成工程的(见
 *   scripts/build_android.sh),仓库里只有 `app/src/main/`,没有 `build.gradle`。
 *   引入第三方库要改 build.gradle,而那份文件属于生成工程、不入库 —— 于是依赖
 *   声明会成为一个无法版本控制的隐式前提。用 java.net.ServerSocket 纯手写就没有
 *   这个问题:零依赖,叠加规则不变。
 *
 *   代价是这段代码要自己保证健壮性,所以下面刻意写得保守:单线程 accept、
 *   线程池处理连接、每个请求都设超时、任何异常都不让服务线程退出。
 *
 * 为什么不用 Capacitor 的 server.url 直接把 WebView 指过来:
 *
 *   那会把 Capacitor 的桥也一起换掉,而蓝牙打印眼下是通的 —— 不值得冒这个险。
 *   现在 WebView 仍从 APK 资源加载,只是把 API 请求打到 http://localhost:8000,
 *   两边靠 CORS 放行。这样服务没起来时页面照常显示(只是报连不上),而不是白屏。
 *
 * English
 * -------
 * Serves the web UI and the API from the device itself, so the tablet becomes a
 * printing node: the DE1 uploads shot JSON straight to it and it renders and prints
 * over Bluetooth, with no computer involved.
 *
 * Why hand-rolled rather than NanoHTTPD or similar:
 *
 *   The native sources are *overlaid* onto the Capacitor-generated project (see
 *   scripts/build_android.sh); the repository holds only `app/src/main/`, not
 *   `build.gradle`. Adding a library means editing build.gradle, which belongs to the
 *   generated project and is not tracked — so the dependency would become an implicit,
 *   unversioned precondition. Plain java.net.ServerSocket has none of that: zero
 *   dependencies, the overlay rule stays as it is.
 *
 *   The cost is that this code has to be robust on its own, hence the deliberately
 *   conservative shape below: a single accept loop, a thread pool for connections, a
 *   timeout on every request, and no exception ever allowed to kill the server thread.
 *
 * Why not point Capacitor's server.url straight at this server:
 *
 *   That would swap out Capacitor's bridge too, and Bluetooth printing currently
 *   works — not worth the risk. The WebView still loads from the APK assets and simply
 *   sends its API calls to http://localhost:8000, with CORS to allow it. If the server
 *   is not up, the page still renders (it just reports it cannot connect) rather than
 *   showing a blank error page.
 */
public class MiniHttpServer {

    private static final String TAG = "PTSHttp";

    /** 请求处理器 / a request handler. */
    public interface Handler {
        Response handle(Request req) throws Exception;
    }

    /** 一次请求 / one request. */
    public static class Request {
        public String method = "GET";
        public String path = "/";
        public String query = "";
        public final Map<String, String> headers = new HashMap<>();
        public byte[] body = new byte[0];
        public String clientIp = "";

        /** 取查询参数,没有则返回默认值 / a query parameter, or the fallback. */
        public String param(String name, String fallback) {
            if (query == null || query.isEmpty()) return fallback;
            for (String pair : query.split("&")) {
                int eq = pair.indexOf('=');
                if (eq < 0) continue;
                if (!pair.substring(0, eq).equals(name)) continue;
                try {
                    return URLDecoder.decode(pair.substring(eq + 1), "UTF-8");
                } catch (Exception e) {
                    return fallback;
                }
            }
            return fallback;
        }

        public String bodyText() {
            return new String(body, StandardCharsets.UTF_8);
        }
    }

    /** 一次响应 / one response. */
    public static class Response {
        public int status = 200;
        public String contentType = "text/plain; charset=utf-8";
        public byte[] body = new byte[0];
        public final Map<String, String> extraHeaders = new HashMap<>();

        public static Response text(int status, String s) {
            Response r = new Response();
            r.status = status;
            r.body = s.getBytes(StandardCharsets.UTF_8);
            return r;
        }

        public static Response json(int status, String s) {
            Response r = text(status, s);
            r.contentType = "application/json; charset=utf-8";
            return r;
        }

        public static Response bytes(int status, String type, byte[] data) {
            Response r = new Response();
            r.status = status;
            r.contentType = type;
            r.body = data;
            return r;
        }
    }

    private final int port;
    private final Handler handler;
    private ServerSocket serverSocket;
    private Thread acceptThread;
    private ExecutorService pool;
    private volatile boolean running = false;

    public MiniHttpServer(int port, Handler handler) {
        this.port = port;
        this.handler = handler;
    }

    public boolean isRunning() {
        return running;
    }

    public int getPort() {
        return port;
    }

    /** 启动。失败时返回 false,不抛异常 —— 调用方通常是 Activity,不该因为服务起不来而崩。 */
    /** Start. Returns false instead of throwing: callers are usually Activities. */
    public synchronized boolean start() {
        if (running) return true;
        try {
            serverSocket = new ServerSocket();
            // 绑 0.0.0.0:局域网上别的设备要能访问 —— 这正是这个服务存在的意义。
            // Bind 0.0.0.0: other devices on the LAN must reach it; that is the point.
            serverSocket.setReuseAddress(true);
            serverSocket.bind(new InetSocketAddress("0.0.0.0", port));
        } catch (IOException e) {
            Log.e(TAG, "绑定端口失败 / bind failed: " + e.getMessage());
            return false;
        }

        // Android 上日志里最常见的误判是「端口被占用」和「被系统限制」,
        // 这里显式区分一下,方便排查。
        // On Android the usual confusion is "port in use" versus "blocked by the
        // system"; say which.
        pool = Executors.newCachedThreadPool();
        running = true;
        acceptThread = new Thread(this::acceptLoop, "pts-http-accept");
        acceptThread.setDaemon(true);
        acceptThread.start();
        Log.i(TAG, "HTTP 服务已启动 / server listening on 0.0.0.0:" + port);
        return true;
    }

    public synchronized void stop() {
        running = false;
        try {
            if (serverSocket != null) serverSocket.close();
        } catch (IOException ignored) {
        }
        serverSocket = null;
        if (pool != null) {
            pool.shutdownNow();
            pool = null;
        }
        if (acceptThread != null) {
            acceptThread.interrupt();
            acceptThread = null;
        }
    }

    private void acceptLoop() {
        while (running) {
            Socket socket = null;
            try {
                socket = serverSocket.accept();
                socket.setSoTimeout(15000);
                final Socket s = socket;
                pool.execute(() -> handleConnection(s));
            } catch (IOException e) {
                // 停止时 accept 会抛,是正常的;运行中抛了也继续转,不能因为一次
                // 连接失败就把整个服务停掉。
                // accept throws on shutdown, which is expected; if it throws while
                // running we still keep going — one bad connection must not stop the
                // service.
                if (running) Log.w(TAG, "accept 失败 / accept failed: " + e.getMessage());
            } catch (Exception e) {
                if (socket != null) {
                    try { socket.close(); } catch (IOException ignored) { }
                }
            }
        }
    }

    private void handleConnection(Socket socket) {
        try (Socket s = socket;
             InputStream in = new BufferedInputStream(s.getInputStream());
             OutputStream out = new BufferedOutputStream(s.getOutputStream())) {

            Request req = readRequest(in, s);
            if (req == null) return;

            Response resp;
            try {
                resp = handler.handle(req);
                if (resp == null) resp = Response.text(500, "no response");
            } catch (Exception e) {
                Log.e(TAG, "处理请求出错 / handler error: " + e);
                resp = Response.text(500, "internal error: " + e.getMessage());
            }

            writeResponse(out, req, resp);
        } catch (Exception e) {
            Log.w(TAG, "连接处理失败 / connection failed: " + e.getMessage());
        }
    }

    private Request readRequest(InputStream in, Socket s) throws IOException {
        Request req = new Request();
        req.clientIp = s.getInetAddress() != null ? s.getInetAddress().getHostAddress() : "";

        // 请求行 / request line
        String line = readLine(in);
        if (line == null || line.isEmpty()) return null;
        String[] parts = line.split(" ");
        if (parts.length < 2) return null;
        req.method = parts[0].toUpperCase(Locale.US);
        String target = parts[1];
        int q = target.indexOf('?');
        if (q >= 0) {
            req.path = target.substring(0, q);
            req.query = target.substring(q + 1);
        } else {
            req.path = target;
        }
        try {
            req.path = URLDecoder.decode(req.path, "UTF-8");
        } catch (Exception ignored) { }

        // 头部 / headers
        String h;
        int contentLength = 0;
        while ((h = readLine(in)) != null && !h.isEmpty()) {
            int c = h.indexOf(':');
            if (c <= 0) continue;
            String name = h.substring(0, c).trim().toLowerCase(Locale.US);
            String value = h.substring(c + 1).trim();
            req.headers.put(name, value);
            if (name.equals("content-length")) {
                try { contentLength = Integer.parseInt(value.trim()); }
                catch (NumberFormatException ignored) { }
            }
        }

        // 请求体。设上限,避免一个畸形请求就把内存吃光。
        // Body, with a cap so one malformed request cannot eat all the memory.
        if (contentLength > 0) {
            final int MAX = 64 * 1024 * 1024;
            if (contentLength > MAX) throw new IOException("body too large: " + contentLength);
            req.body = readN(in, contentLength);
        }
        return req;
    }

    private void writeResponse(OutputStream out, Request req, Response resp) throws IOException {
        ByteArrayOutputStream head = new ByteArrayOutputStream();
        String statusText = statusText(resp.status);
        head.write(("HTTP/1.1 " + resp.status + " " + statusText + "\r\n")
                .getBytes(StandardCharsets.UTF_8));
        head.write(("Content-Type: " + resp.contentType + "\r\n")
                .getBytes(StandardCharsets.UTF_8));
        head.write(("Content-Length: " + resp.body.length + "\r\n")
                .getBytes(StandardCharsets.UTF_8));
        head.write("Cache-Control: no-store\r\n".getBytes(StandardCharsets.UTF_8));
        // 关闭连接而不是保持:实现简单,而我们的请求量很小
        // Close rather than keep-alive: simpler, and our request volume is tiny
        head.write("Connection: close\r\n".getBytes(StandardCharsets.UTF_8));

        // CORS —— App 自己的 WebView 从资源加载,发 API 请求时算跨源(端口不同)。
        // 局域网里别的设备访问时同样需要。
        //
        // CORS: the app's own WebView loads from the APK assets, so its API calls are
        // cross-origin (different port). Devices on the LAN need this too.
        String origin = req.headers.get("origin");
        head.write(("Access-Control-Allow-Origin: " + (origin != null ? origin : "*") + "\r\n")
                .getBytes(StandardCharsets.UTF_8));
        head.write("Access-Control-Allow-Methods: GET, POST, DELETE, OPTIONS\r\n"
                .getBytes(StandardCharsets.UTF_8));
        head.write("Access-Control-Allow-Headers: Content-Type\r\n"
                .getBytes(StandardCharsets.UTF_8));
        head.write("Access-Control-Max-Age: 600\r\n".getBytes(StandardCharsets.UTF_8));
        for (Map.Entry<String, String> e : resp.extraHeaders.entrySet()) {
            head.write((e.getKey() + ": " + e.getValue() + "\r\n")
                    .getBytes(StandardCharsets.UTF_8));
        }
        head.write("\r\n".getBytes(StandardCharsets.UTF_8));

        out.write(head.toByteArray());
        if (resp.body.length > 0) out.write(resp.body);
        out.flush();
    }

    private static String statusText(int code) {
        switch (code) {
            case 200: return "OK";
            case 204: return "No Content";
            case 400: return "Bad Request";
            case 403: return "Forbidden";
            case 404: return "Not Found";
            case 405: return "Method Not Allowed";
            case 413: return "Payload Too Large";
            case 500: return "Internal Server Error";
            default:  return "OK";
        }
    }

    private static String readLine(InputStream in) throws IOException {
        ByteArrayOutputStream buf = new ByteArrayOutputStream(128);
        int b;
        while ((b = in.read()) != -1) {
            if (b == '\n') {
                byte[] a = buf.toByteArray();
                int len = a.length;
                if (len > 0 && a[len - 1] == '\r') len--;
                return new String(a, 0, len, StandardCharsets.UTF_8);
            }
            buf.write(b);
            if (buf.size() > 8192) throw new IOException("header line too long");
        }
        return buf.size() == 0 ? null : new String(buf.toByteArray(), StandardCharsets.UTF_8);
    }

    private static byte[] readN(InputStream in, int n) throws IOException {
        byte[] out = new byte[n];
        int off = 0;
        while (off < n) {
            int read = in.read(out, off, n - off);
            if (read < 0) throw new IOException("unexpected end of body");
            off += read;
        }
        return out;
    }

    /** 解析一行 "application/json" 之类的头值里的键值对,供 multipart 用。 */
    /** Parse a header value like "application/json" into its parameters. */
    public static Map<String, String> parseHeaderParams(String value) {
        Map<String, String> out = new HashMap<>();
        if (value == null) return out;
        List<String> parts = new ArrayList<>();
        for (String p : value.split(";")) parts.add(p.trim());
        for (String p : parts) {
            int eq = p.indexOf('=');
            if (eq > 0) {
                out.put(p.substring(0, eq).trim().toLowerCase(Locale.US),
                        p.substring(eq + 1).trim().replace("\"", ""));
            }
        }
        return out;
    }
}
