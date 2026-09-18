package com.printtheshot.printer;

import android.util.Base64;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.Callable;

/**
 * 本机 HTTP 打印桥 / Loopback HTTP printing bridge
 * ==============================================================================
 *
 * 中文
 * ----
 * 只监听 127.0.0.1 的一个小 HTTP 服务,给「Python 服务端也跑在这台 Android 上」
 * 的部署方式用:服务端的 /api/print 把拼好的 ESC/POS 字节 POST 过来,由原生层
 * 写进蓝牙 socket。数据压根不经过网络,跟 JS 插件走的是同一个
 * {@link BluetoothPrinter} 单例,也就是同一条蓝牙连接。
 * A small HTTP server bound to 127.0.0.1 only, for deployments where the
 * Python service also runs on this Android device: the service POSTs the
 * assembled ESC/POS bytes to /api/print and the native layer writes them into
 * the Bluetooth socket. Nothing leaves the device, and it shares the same
 * {@link BluetoothPrinter} singleton — i.e. the same Bluetooth link — with the
 * JS plugin.
 *
 * 接口 / endpoints:
 * <pre>
 *   GET  /ping      → 200 {"ok":true,...}                      探活
 *   GET  /printers  → {"printers":[{address,name,paired,default}]}
 *   POST /print     → {"success":bool,"message":str,"printer":str}
 *                     body: {"data":"&lt;base64 ESC/POS&gt;","address":"AA:BB:.."}
 * </pre>
 *
 * 安全边界 / security boundary:
 *   - 只绑回环地址,局域网里别的机器连不上;
 *   - **没有鉴权**,因为调用方是同一台机器上的本地服务,而设备上任何 App 都能
 *     连回环端口。风险是「同一个设备上的恶意 App 可以打一张小票」,对一个专用
 *     打印 App 来说可以接受;要彻底关掉,起前台服务时把 http_bridge 传 false。
 *   - Loopback only: nothing on the LAN can reach it.
 *   - **No authentication**, because the caller is a local service on the same
 *     device — and note that any app on the device can reach a loopback port.
 *     The exposure is "a malicious app on this device could print a receipt",
 *     which is acceptable for a single-purpose printing app; pass
 *     http_bridge=false when starting the service to switch it off entirely.
 *
 * 并发 / concurrency:
 *   一次只处理一个连接(accept 之后同步处理),打印任务天然串行,不会有两份
 *   数据交叉写进同一条蓝牙连接。
 *   One connection at a time (handled synchronously after accept), so print
 *   jobs are serialised by construction and two payloads can never interleave
 *   on the same Bluetooth link.
 *
 * English
 * -------
 * Started by {@link PrinterService} (on by default). See the endpoints and the
 * security notes above.
 */
public final class LocalPrintBridge {

    private static final String TAG = BluetoothPrinter.TAG;

    /** 监听地址:只绑回环 / listen address, loopback only. */
    public static final String HOST = "127.0.0.1";

    /** 默认端口 / default port. */
    public static final int DEFAULT_PORT = 9100;

    /** 请求体上限(字节),防止有人一次塞爆内存 / request body cap, in bytes. */
    private static final int MAX_BODY_BYTES = 8 * 1024 * 1024;

    /** 单行请求头上限(字节)/ cap on one header line. */
    private static final int MAX_LINE_BYTES = 8192;

    /** socket 读写超时(毫秒)/ socket read/write timeout, ms. */
    private static final int SOCKET_TIMEOUT_MS = 15000;

    /** 枚举打印机的超时(毫秒)/ timeout for enumerating printers, ms. */
    private static final long PRINTERS_TIMEOUT_MS = 10000L;

    /** 等待队列长度,只服务本机一个客户端,给 4 就够 / backlog, loopback only. */
    private static final int BACKLOG = 4;

    private final int port;

    private ServerSocket serverSocket;
    private Thread acceptor;
    private volatile boolean running;

    /** 用默认端口 / use the default port. */
    public LocalPrintBridge() {
        this(DEFAULT_PORT);
    }

    /** 指定端口 / use a specific port. */
    public LocalPrintBridge(int port) {
        this.port = port;
    }

    /** 是否在监听 / whether the bridge is listening. */
    public boolean isRunning() {
        return running;
    }

    /** 实际监听的端口 / the port actually listened on. */
    public int getPort() {
        return port;
    }

    /**
     * 开始监听 / start listening.
     *
     * 幂等;端口被占会抛 IOException,由调用方决定怎么处理。
     * Idempotent; throws IOException when the port is taken, leaving the
     * decision to the caller.
     */
    public void start() throws IOException {
        if (running) {
            return;
        }
        ServerSocket socket = new ServerSocket();
        socket.setReuseAddress(true);
        socket.bind(new InetSocketAddress(InetAddress.getByName(HOST), port), BACKLOG);
        this.serverSocket = socket;
        this.running = true;

        Thread thread = new Thread(new Runnable() {
            @Override
            public void run() {
                acceptLoop();
            }
        }, "printtheshot-http");
        thread.setDaemon(true);
        this.acceptor = thread;
        thread.start();
    }

    /** 停止监听 / stop listening. */
    public void stop() {
        running = false;
        ServerSocket socket = serverSocket;
        serverSocket = null;
        if (socket != null) {
            try {
                socket.close();
            } catch (IOException e) {
                Log.w(TAG, "关闭打印桥失败 / cannot close the bridge: " + e.getMessage());
            }
        }
        acceptor = null;
    }

    // ------------------------------------------------------------------
    // 接收循环 / accept loop
    // ------------------------------------------------------------------

    private void acceptLoop() {
        while (running) {
            ServerSocket socket = serverSocket;
            if (socket == null) {
                break;
            }
            Socket client = null;
            try {
                client = socket.accept();
            } catch (IOException e) {
                if (running) {
                    Log.w(TAG, "accept 失败 / accept failed: " + e.getMessage());
                }
                // 服务被 stop() 关掉时 accept 会抛异常,这里顺势退出 /
                // stop() closes the socket and accept throws; exit the loop
                if (!running) {
                    break;
                }
                continue;
            }

            try {
                handle(client);
            } catch (Exception e) {
                Log.w(TAG, "处理请求失败 / request failed: " + e);
            } finally {
                closeQuietly(client);
            }
        }
    }

    private void handle(Socket client) throws IOException {
        client.setSoTimeout(SOCKET_TIMEOUT_MS);
        InputStream in = new BufferedInputStream(client.getInputStream());
        OutputStream out = new BufferedOutputStream(client.getOutputStream());

        String requestLine = readLine(in);
        if (requestLine == null || requestLine.trim().isEmpty()) {
            respond(out, 400, "Bad Request", errorJson("空请求 / empty request"));
            return;
        }

        String[] parts = requestLine.trim().split("\\s+");
        if (parts.length < 2) {
            respond(out, 400, "Bad Request", errorJson("请求行不完整 / malformed request line"));
            return;
        }
        String method = parts[0].toUpperCase(Locale.US);
        String path = parts[1];
        int query = path.indexOf('?');
        if (query >= 0) {
            path = path.substring(0, query);
        }

        int contentLength = 0;
        String header;
        while ((header = readLine(in)) != null && !header.isEmpty()) {
            String lower = header.toLowerCase(Locale.US);
            if (lower.startsWith("content-length:")) {
                try {
                    contentLength = Integer.parseInt(header.substring("content-length:".length()).trim());
                } catch (NumberFormatException e) {
                    respond(out, 400, "Bad Request", errorJson("Content-Length 不合法 / bad Content-Length"));
                    return;
                }
            }
        }

        if (contentLength < 0 || contentLength > MAX_BODY_BYTES) {
            respond(out, 413, "Payload Too Large",
                    errorJson("请求体过大 / request body too large"));
            return;
        }
        byte[] body = readExactly(in, contentLength);

        if ("/ping".equals(path)) {
            if (!"GET".equals(method)) {
                respond(out, 405, "Method Not Allowed", errorJson("只支持 GET /ping / use GET /ping"));
                return;
            }
            respond(out, 200, "OK", pingJson());
            return;
        }

        if ("/printers".equals(path)) {
            if (!"GET".equals(method)) {
                respond(out, 405, "Method Not Allowed",
                        errorJson("只支持 GET /printers / use GET /printers"));
                return;
            }
            respond(out, 200, "OK", printersJson());
            return;
        }

        if ("/print".equals(path)) {
            if (!"POST".equals(method)) {
                respond(out, 405, "Method Not Allowed", errorJson("只支持 POST /print / use POST /print"));
                return;
            }
            respond(out, 200, "OK", printJson(body));
            return;
        }

        respond(out, 404, "Not Found",
                errorJson("没有这个接口 / no such endpoint: " + path));
    }

    // ------------------------------------------------------------------
    // 各接口实现 / endpoint implementations
    // ------------------------------------------------------------------

    private String pingJson() {
        try {
            JSONObject json = new JSONObject();
            json.put("ok", true);
            json.put("service", "PrintTheShot");
            json.put("port", port);
            String address = BluetoothPrinter.get().getDefaultAddress();
            json.put("defaultPrinter", address == null ? "" : address);
            json.put("connected", BluetoothPrinter.get().isConnected());
            return json.toString();
        } catch (JSONException e) {
            return errorJson("组装响应失败 / cannot build the response");
        }
    }

    private String printersJson() {
        JSONObject root = new JSONObject();
        try {
            // 蓝牙操作必须在工作队列上跑,这里同步等结果 /
            // Bluetooth work must run on the worker queue; wait for the result
            List<BluetoothPrinter.PrinterEntry> entries = BluetoothPrinter.get()
                    .submitAndWait(new Callable<List<BluetoothPrinter.PrinterEntry>>() {
                        @Override
                        public List<BluetoothPrinter.PrinterEntry> call() {
                            return BluetoothPrinter.get().listPrinters();
                        }
                    }, PRINTERS_TIMEOUT_MS);

            JSONArray array = new JSONArray();
            for (BluetoothPrinter.PrinterEntry entry : entries) {
                JSONObject item = new JSONObject();
                item.put("address", entry.address);
                item.put("name", entry.displayName());
                item.put("paired", entry.paired);
                item.put("default", entry.isDefault);
                item.put("status", entry.isDefault ? "default" : "paired");
                array.put(item);
            }
            root.put("printers", array);
            root.put("success", true);
        } catch (Exception e) {
            Log.w(TAG, "枚举打印机失败 / cannot enumerate printers: " + e);
            try {
                root.put("printers", new JSONArray());
                root.put("success", false);
                root.put("message", "枚举打印机失败 / cannot enumerate printers: " + e.getMessage());
            } catch (JSONException ignored) {
                return errorJson("组装响应失败 / cannot build the response");
            }
        }
        return root.toString();
    }

    private String printJson(byte[] body) {
        JSONObject result = new JSONObject();
        try {
            if (body == null || body.length == 0) {
                return errorJson("请求体为空 / empty request body");
            }
            JSONObject request = new JSONObject(new String(body, StandardCharsets.UTF_8));
            String data = request.optString("data", "");
            String address = request.optString("address", "");

            if (data.isEmpty()) {
                return errorJson("缺少 data 字段 / missing 'data' field");
            }

            final byte[] payload;
            try {
                payload = Base64.decode(data, Base64.DEFAULT);
            } catch (IllegalArgumentException e) {
                return errorJson("data 不是合法 base64 / 'data' is not valid base64");
            }

            final String target = address;
            Boolean ok = BluetoothPrinter.get().submitAndWait(new Callable<Boolean>() {
                @Override
                public Boolean call() {
                    return Boolean.valueOf(BluetoothPrinter.get().printJob(payload, target));
                }
            }, BluetoothPrinter.timeoutForPayload(payload.length));

            boolean success = ok != null && ok.booleanValue();
            result.put("success", success);
            result.put("message", success
                    ? "已发送 " + payload.length + " 字节 / sent " + payload.length + " bytes"
                    : BluetoothPrinter.get().getLastError());
            String used = BluetoothPrinter.get().getConnectedAddress();
            result.put("printer", used == null ? target : used);
            return result.toString();
        } catch (JSONException e) {
            return errorJson("请求体不是合法 JSON / request body is not valid JSON");
        } catch (Exception e) {
            Log.w(TAG, "打印失败 / print failed: " + e);
            try {
                result.put("success", false);
                result.put("message", "打印失败 / print failed: " + e.getMessage());
                return result.toString();
            } catch (JSONException ignored) {
                return errorJson("打印失败 / print failed");
            }
        }
    }

    // ------------------------------------------------------------------
    // HTTP 小工具 / tiny HTTP helpers
    // ------------------------------------------------------------------

    /**
     * 读一行(以 \n 结尾)/ read one line terminated by \n.
     *
     * 返回 null 表示对端已经关掉了 / null means the peer closed the stream.
     */
    private static String readLine(InputStream in) throws IOException {
        ByteArrayOutputStream buffer = new ByteArrayOutputStream(128);
        int b;
        while ((b = in.read()) != -1) {
            if (b == '\n') {
                break;
            }
            if (b != '\r') {
                buffer.write(b);
            }
            if (buffer.size() > MAX_LINE_BYTES) {
                throw new IOException("请求头过长 / header line too long");
            }
        }
        if (b == -1 && buffer.size() == 0) {
            return null;
        }
        return new String(buffer.toByteArray(), StandardCharsets.UTF_8);
    }

    /** 精确读 length 个字节 / read exactly length bytes. */
    private static byte[] readExactly(InputStream in, int length) throws IOException {
        if (length <= 0) {
            return new byte[0];
        }
        byte[] data = new byte[length];
        int read = 0;
        while (read < length) {
            int n = in.read(data, read, length - read);
            if (n < 0) {
                throw new IOException("请求体不完整 / truncated request body");
            }
            read += n;
        }
        return data;
    }

    /** 回一个 JSON 响应 / write a JSON response. */
    private static void respond(OutputStream out, int status, String reason, String json) throws IOException {
        byte[] payload = json.getBytes(StandardCharsets.UTF_8);
        StringBuilder head = new StringBuilder();
        head.append("HTTP/1.1 ").append(status).append(' ').append(reason).append("\r\n");
        head.append("Content-Type: application/json; charset=utf-8\r\n");
        head.append("Content-Length: ").append(payload.length).append("\r\n");
        head.append("Cache-Control: no-store\r\n");
        // 一次一个连接,回完就关,省得处理 keep-alive /
        // one connection at a time, closed after the response: no keep-alive
        head.append("Connection: close\r\n\r\n");
        out.write(head.toString().getBytes(StandardCharsets.US_ASCII));
        out.write(payload);
        out.flush();
    }

    private static String errorJson(String message) {
        try {
            JSONObject json = new JSONObject();
            json.put("success", false);
            json.put("message", message);
            return json.toString();
        } catch (JSONException e) {
            return "{\"success\":false}";
        }
    }

    private static void closeQuietly(Socket socket) {
        if (socket == null) {
            return;
        }
        try {
            socket.close();
        } catch (IOException e) {
            Log.w(TAG, "关闭连接失败 / cannot close the connection: " + e.getMessage());
        }
    }
}
