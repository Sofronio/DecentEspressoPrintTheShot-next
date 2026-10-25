package com.printtheshot.server;

import android.content.Context;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.nio.file.Files;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Date;
import java.util.HashSet;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;

/**
 * Shot 数据存储 / shot storage
 * ============================
 *
 * 中文
 * ----
 * 把上传来的 shot JSON 存在应用私有目录里,并维护一份索引。对应 Python 服务端的
 * shots_data/ + index.json。
 *
 * 放在 getFilesDir() 而不是外部存储:不需要任何存储权限,卸载时自动清理,
 * 而且 Android 10 之后外部存储本来就受限(分区存储)。**这条很重要** ——
 * 走外部存储的话就要申请 MANAGE_EXTERNAL_STORAGE 之类的权限,那是要上架审核
 * 解释的重权限,而我们只是存几个 JSON 文件。
 *
 * English
 * -------
 * Stores uploaded shot JSON in the app's private directory and keeps an index,
 * mirroring shots_data/ + index.json on the Python side.
 *
 * Private storage rather than external: no storage permission required, cleaned up on
 * uninstall, and external storage is restricted since Android 10 (scoped storage).
 * **This matters** — external storage would mean asking for permissions like
 * MANAGE_EXTERNAL_STORAGE, which needs justification in store review, and all we are
 * doing is keeping a few JSON files.
 */
public class ShotStore {

    private static final String TAG = "PTSStore";
    private static final int MAX_SHOTS = 5000;
    private static final int MAX_PENDING = 50;

    /**
     * 打印去重窗口 / the print de-duplication window.
     *
     * 一份**内容相同**的 shot 在这么久之内不再打印第二次。
     *
     * A shot with identical **content** is not printed again within this window.
     */
    static final long DEDUPE_WINDOW_MS = 30_000;

    private final Context ctx;
    private final File dir;
    private final File indexFile;
    private final Object lock = new Object();

    /** 内存索引,和文件里的 index.json 保持一致 / in-memory index, mirrored to index.json. */
    private final List<JSONObject> index = new ArrayList<>();

    /** 待打印队列 / the pending-print queue. */
    private final List<JSONObject> pending = new ArrayList<>();

    /**
     * 后台打印正在处理的任务 / jobs currently owned by the background-print path.
     *
     * 被认领的任务仍然留在 pending 里(去重、超时都还要看它),但**不返回给前端** ——
     * 否则 App 在打印途中被切回前台,前台的异步泵会再打一遍。
     *
     * Claimed jobs stay in `pending` (de-duplication and expiry still see them) but are
     * **not returned to the front end**, or a background print that is still in flight
     * would be printed a second time if the app comes back to the foreground.
     */
    private final Set<String> backgroundClaims = new HashSet<>();

    /**
     * 内容哈希 → 最近一次**打印成功**的时刻 / content hash → when it was last printed.
     *
     * 为什么按内容而不是按文件名去重:每一次上传都会生成一个新文件名(带微秒 ID),
     * 所以同一个 shot 被传三次就是三个不同的名字,按名字拦不住。而上游确实会重复
     * 上传 —— DE1 侧的 after_flow_complete 可能重复触发,插件里那个
     * last_upload_shot 只赋值、从不比较,HTTP 超时重试时服务端其实也已经存盘了。
     * 结果就是同一张票出好几次。
     *
     * Why content rather than filename: every upload gets a fresh filename (it carries a
     * microsecond ID), so one shot uploaded three times is three different names and a
     * name-based check catches nothing. And the upstream really does repeat — the DE1's
     * after_flow_complete can fire more than once, the plugin's last_upload_shot is
     * assigned but never compared, and an HTTP timeout retry arrives after the server
     * already stored the shot. The result is the same receipt coming out several times.
     */
    private final Map<String, Long> printedAt = new LinkedHashMap<>();

    public ShotStore(Context ctx) {
        this.ctx = ctx;
        dir = new File(ctx.getFilesDir(), "shots_data");
        if (!dir.exists()) dir.mkdirs();
        indexFile = new File(dir, "index.json");
        loadIndex();
    }

    public File getDir() {
        return dir;
    }

    // ---------------------------------------------------------------- 索引
    // ---------------------------------------------------------------- index

    private void loadIndex() {
        // index.json 优先;崩溃后如果索引没写成,就扫描目录兜底 —— 和 Python 端
        // 一样的自愈策略,理由也一样:数据比索引重要。
        //
        // index.json first; if a crash lost the index, fall back to scanning the
        // directory — the same self-healing the Python side does, for the same reason:
        // the data matters more than the index.
        synchronized (lock) {
            try {
                if (indexFile.exists()) {
                    JSONArray arr = new JSONArray(
                            new String(Files.readAllBytes(indexFile.toPath()), StandardCharsets.UTF_8));
                    for (int i = 0; i < arr.length(); i++) index.add(arr.getJSONObject(i));
                }
            } catch (Exception e) {
                Log.w(TAG, "索引读取失败,将重建 / index unreadable, rebuilding: " + e.getMessage());
                index.clear();
            }

            Set<String> known = new HashSet<>();
            for (JSONObject o : index) known.add(o.optString("filename"));

            File[] files = dir.listFiles();
            if (files != null) {
                List<File> sorted = new ArrayList<>();
                for (File f : files) {
                    if (f.isFile() && f.getName().endsWith(".json")
                            && !f.getName().equals("index.json")
                            && !known.contains(f.getName())) {
                        sorted.add(f);
                    }
                }
                Collections.sort(sorted);
                for (File f : sorted) {
                    try {
                        JSONObject meta = metaFromFile(f, "UNKNOWN", f.length());
                        index.add(meta);
                    } catch (Exception e) {
                        Log.w(TAG, "跳过无法解析的文件 / skipping unparseable file: " + f.getName());
                    }
                }
            }
            sortIndex();
            persistIndex();
        }
    }

    /** 从文件内容生成一条索引记录 / build one index entry from a shot file. */
    private JSONObject metaFromFile(File f, String machineId, long size) throws Exception {
        JSONObject shot = new JSONObject(
                new String(Files.readAllBytes(f.toPath()), StandardCharsets.UTF_8));
        JSONObject meta = new JSONObject();
        meta.put("filename", f.getName());
        meta.put("data_size", size);
        meta.put("machine_id", machineId);
        meta.put("bean", shot.optJSONObject("meta") != null
                && shot.optJSONObject("meta").optJSONObject("bean") != null
                ? shot.optJSONObject("meta").optJSONObject("bean").optString("type", "未知")
                : "未知");
        meta.put("profile", shot.optJSONObject("profile") != null
                ? shot.optJSONObject("profile").optString("title", "unknown") : "unknown");
        meta.put("clock", shot.optString("clock", "unknown"));

        // 时间戳:优先用文件名里那一段(上传时刻),它和文档里列出的时间一致。
        // The timestamp comes from the filename (the upload moment), matching what the
        // listing shows.
        String ts = timestampFromName(f.getName());
        meta.put("timestamp", ts);
        return meta;
    }

    private static String timestampFromName(String name) {
        // shot_20260920_161234_... -> 20260920_161234
        String s = name.startsWith("shot_") ? name.substring(5) : name;
        String[] parts = s.split("_");
        if (parts.length >= 2) return parts[0] + "_" + parts[1];
        return s;
    }

    private void sortIndex() {
        Collections.sort(index, (a, b) ->
                b.optString("timestamp").compareTo(a.optString("timestamp")));
        while (index.size() > MAX_SHOTS) index.remove(index.size() - 1);
    }

    private void persistIndex() {
        try (FileOutputStream out = new FileOutputStream(indexFile)) {
            JSONArray arr = new JSONArray();
            for (JSONObject o : index) arr.put(o);
            out.write(arr.toString().getBytes(StandardCharsets.UTF_8));
        } catch (Exception e) {
            Log.w(TAG, "索引持久化失败 / persist failed: " + e.getMessage());
        }
    }

    // ---------------------------------------------------------------- 写入
    // ---------------------------------------------------------------- write

    /**
     * 保存一条上传的 shot / save one uploaded shot.
     *
     * 文件名带微秒级 ID,避免同一秒内两次上传撞名 —— Python 端踩过这个坑,
     * 而且症状是「一条数据凭空消失」,很难查。
     *
     * The filename carries a microsecond ID so two uploads in the same second cannot
     * collide — the Python side hit that, and the symptom was a shot silently
     * disappearing, which is hard to trace.
     *
     * queueForPrint = false 时只存不排队:那是界面上「禁用打印」开关的位置。关掉
     * 之后数据照收照存(列表里看得到),只是不再自动出纸;手动按打印照常能打。
     * Python 端一直是这个语义(见 handle_upload 里的 `if PRINT_ENABLED`)。
     *
     * queueForPrint = false stores without queueing, which is what the "printing
     * disabled" switch does. Data still arrives and is still listed; it just stops
     * printing by itself. Pressing print by hand still works. The Python side has
     * always behaved this way (see `if PRINT_ENABLED` in handle_upload).
     */
    public JSONObject save(byte[] body, String machineId, boolean queueForPrint) throws Exception {
        // 先解析,确认真的是 JSON —— 存进去一个坏文件只会让后面每次列表都出错。
        // Parse first: storing a malformed file would break every later listing.
        JSONObject shot = new JSONObject(new String(body, StandardCharsets.UTF_8));

        String stamp = new SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US).format(new Date());
        String id = String.valueOf(System.nanoTime() / 1000);
        String filename = "shot_" + stamp + "_" + id + ".json";

        File f = new File(dir, filename);
        try (FileOutputStream out = new FileOutputStream(f)) {
            out.write(body);
        }

        synchronized (lock) {
            JSONObject meta = metaFromFile(f, machineId, body.length);
            index.add(meta);
            sortIndex();
            persistIndex();
            // 机器名一起带上:它只在这个索引里,打印那一端拿不到别的来源
            // Carry the machine name; the index is the only place it exists
            if (queueForPrint && !pendingContains(filename)) queuePrintLocked(filename, machineId);
            return meta;
        }
    }

    /**
     * 导入一条备份里的 shot / import one shot out of a backup archive.
     *
     * 和 save() 有两处刻意的区别:
     *
     * 1. **按文件名覆盖**,而不是另生成一个名字 —— 导入是「恢复」,同名就意味着
     *    回到包里存的那一份。这也是它和上传唯一该有的语义差别。
     * 2. **不入打印队列**。save() 会入队,是因为它经手的每一条都是刚从机器上打
     *    出来的、正等着出纸;恢复历史不是新数据,不该因为它而吐纸。
     *
     * 机器名记 UNKNOWN:它只存在于索引里,备份包里没有这个信息 —— 和 loadIndex()
     * 目录扫描兜底时的做法一致。
     *
     * Two deliberate differences from save():
     *
     * 1. It overwrites by filename rather than minting a new one — an import is a
     *    restore, so a matching name means going back to the archived copy. This is the
     *    one semantic difference there should be between an import and an upload.
     * 2. It does not queue a print. save() queues because everything it handles came off
     *    the machine just now and is waiting for paper; restoring history is not new
     *    data and must not produce paper.
     *
     * The machine ID is recorded as UNKNOWN: it lives only in the index and a backup
     * archive does not carry it — the same choice loadIndex() makes when it falls back to
     * scanning the directory.
     */
    public JSONObject importFile(String filename, byte[] body) throws Exception {
        // 名字再取一次 basename —— 调用方已经取过,这里再取一次是因为这是最后一道
        // 关口:写盘的地方自己负责,不能指望上游。`../` 在这里被彻底挡掉。
        //
        // Take the basename again. The caller already did, but this is the last gate
        // before the disk and it does not trust upstream to have done it: a `../` dies
        // here.
        String safe = new File(filename).getName();
        JSONObject shot = new JSONObject(new String(body, StandardCharsets.UTF_8));

        File f = new File(dir, safe);
        try (FileOutputStream out = new FileOutputStream(f)) {
            out.write(body);
        }

        synchronized (lock) {
            // 同名旧条目先摘掉,否则索引里会留下两条指向同一个文件的记录
            // Drop any same-named entry first, or the index keeps two records pointing
            // at one file.
            for (int i = index.size() - 1; i >= 0; i--) {
                if (safe.equals(index.get(i).optString("filename"))) {
                    index.remove(i);
                }
            }
            JSONObject meta = metaFromFile(f, "UNKNOWN", body.length);
            index.add(meta);
            sortIndex();
            persistIndex();
            return meta;
        }
    }

    public File fileFor(String name) {
        // basename:文件名来自 URL 查询参数,不能让它跳出数据目录
        // basename only: the name comes from a URL query parameter and must not escape
        String base = new File(name).getName();
        if (!base.endsWith(".json")) return null;
        return new File(dir, base);
    }

    // ---------------------------------------------------------------- 读取
    // ---------------------------------------------------------------- read

    /** 列表(可按日期过滤)/ the listing, optionally filtered by date. */
    public JSONObject list(String dateYyyyMmDd) {
        synchronized (lock) {
            pruneExpiredLocked(System.currentTimeMillis());
            JSONArray out = new JSONArray();
            Set<String> dates = new TreeSet<>(Collections.reverseOrder());
            for (JSONObject o : index) {
                String ts = o.optString("timestamp", "");
                if (ts.length() >= 8) dates.add(ts.substring(0, 8));
            }
            int count = 0;
            for (JSONObject o : index) {
                String ts = o.optString("timestamp", "");
                if (!dateYyyyMmDd.isEmpty()) {
                    if (!ts.startsWith(dateYyyyMmDd)) continue;
                } else if (count >= 100) {
                    break;
                }
                out.put(o);
                count++;
            }
            JSONObject wrapper = new JSONObject();
            try {
                wrapper.put("shots", out);
                JSONArray dl = new JSONArray();
                for (String d : dates) {
                    if (d.length() == 8) dl.put(d.substring(0, 4) + "-" + d.substring(4, 6) + "-" + d.substring(6, 8));
                }
                wrapper.put("dates", dl);
                wrapper.put("pending_prints", pendingFilenames());
            } catch (Exception ignored) { }
            return wrapper;
        }
    }

    /** 统计 / statistics. */
    public JSONObject stats() {
        synchronized (lock) {
            pruneExpiredLocked(System.currentTimeMillis());
            Map<String, Integer> byDate = new LinkedHashMap<>();
            Map<String, Integer> byProfile = new LinkedHashMap<>();
            Map<String, Integer> byBean = new LinkedHashMap<>();
            Set<String> machines = new HashSet<>();
            long totalSize = 0;

            for (JSONObject o : index) {
                String ts = o.optString("timestamp", "");
                if (ts.length() >= 8) {
                    String d = ts.substring(0, 8);
                    String fmt = d.substring(0, 4) + "-" + d.substring(4, 6) + "-" + d.substring(6, 8);
                    byDate.merge(fmt, 1, Integer::sum);
                }
                byProfile.merge(o.optString("profile", "unknown"), 1, Integer::sum);
                byBean.merge(o.optString("bean", "未知"), 1, Integer::sum);
                machines.add(o.optString("machine_id", "UNKNOWN"));
                totalSize += o.optLong("data_size", 0);
            }

            JSONObject out = new JSONObject();
            try {
                out.put("total_shots", index.size());
                out.put("total_dates", byDate.size());
                out.put("machines", machines.size());
                out.put("avg_data_size", index.isEmpty() ? 0 : totalSize / index.size());
                out.put("prints_session", pending.size());

                // 日期分布只列最近 7 天,更早的合并 —— 和桌面端一致,避免列表过长
                // Only the latest 7 days, older ones merged, same as the desktop side
                JSONArray perDate = new JSONArray();
                List<Map.Entry<String, Integer>> entries = new ArrayList<>(byDate.entrySet());
                int older = 0;
                for (int i = 0; i < entries.size(); i++) {
                    if (i < 7) {
                        JSONObject o = new JSONObject();
                        o.put("date", entries.get(i).getKey());
                        o.put("count", entries.get(i).getValue());
                        perDate.put(o);
                    } else {
                        older += entries.get(i).getValue();
                    }
                }
                out.put("per_date", perDate);
                out.put("older_days", Math.max(0, entries.size() - 7));
                out.put("older_count", older);

                out.put("top_profiles", topN(byProfile, 5));
                out.put("top_beans", topN(byBean, 6));
            } catch (Exception ignored) { }
            return out;
        }
    }

    private static JSONArray topN(Map<String, Integer> m, int n) {
        List<Map.Entry<String, Integer>> e = new ArrayList<>(m.entrySet());
        Collections.sort(e, (a, b) -> b.getValue() - a.getValue());
        JSONArray arr = new JSONArray();
        for (int i = 0; i < Math.min(n, e.size()); i++) {
            try {
                JSONObject o = new JSONObject();
                o.put("name", e.get(i).getKey());
                o.put("count", e.get(i).getValue());
                arr.put(o);
            } catch (Exception ignored) { }
        }
        return arr;
    }

    // ---------------------------------------------------------------- 打印队列
    // ---------------------------------------------------------------- print queue

    private boolean pendingContains(String filename) {
        for (JSONObject o : pending) if (filename.equals(o.optString("filename"))) return true;
        return false;
    }

    /**
     * 按内容算哈希 / hash the content of a stored shot.
     *
     * 读文件、算 SHA-256。读不到(文件还没落盘、已被清理)就返回 null —— 拿不到
     * 内容就不参与去重,宁可多打一次也不要因为算不出哈希而漏打。
     *
     * Reads the file and hashes it. Returns null when it cannot be read — no content,
     * no de-duplication: printing twice is better than silently skipping.
     */
    private String contentHash(String filename) {
        try {
            File f = new File(dir, new File(filename).getName());
            if (!f.exists()) return null;
            MessageDigest md = MessageDigest.getInstance("SHA-256");
            byte[] digest = md.digest(Files.readAllBytes(f.toPath()));
            StringBuilder sb = new StringBuilder(digest.length * 2);
            for (byte b : digest) sb.append(String.format("%02x", b));
            return sb.toString();
        } catch (Exception e) {
            return null;
        }
    }

    /** 这份内容刚刚已经打过了吗 / has this content just been printed? */
    private boolean hashPrintedRecentlyLocked(String hash) {
        if (hash == null || hash.isEmpty()) return false;
        Long when = printedAt.get(hash);
        return when != null && (System.currentTimeMillis() - when) <= DEDUPE_WINDOW_MS;
    }

    /**
     * 这份内容已经在队列里等着了吗 / is this content already waiting in the queue?
     *
     * 「已经打印过」拦不住**同时**到达的那几次重复上传:它们在第一份打印完成之前
     * 就都进来了,那时 printedAt 里还没有东西。要在这里按内容再拦一道,队列才不会
     * 一下子排进三份一样的。
     *
     * "Already printed" does nothing about duplicates that arrive *at the same time*:
     * they all land before the first one finishes printing, so printedAt is still empty.
     * Checking the queue by content as well is what keeps three identical copies from
     * piling up in the first place.
     */
    private boolean hashPendingLocked(String hash) {
        if (hash == null || hash.isEmpty()) return false;
        for (JSONObject o : pending) {
            if (hash.equals(o.optString("hash"))) return true;
        }
        return false;
    }

    /**
     * 惰性清理过期条目 / drop entries whose window has passed.
     *
     * 不清理的话,这个 map 会随运行时间一直长 —— 它按内容哈希累积,和 shot 数量
     * 同阶。只在记录时顺手扫一遍,不值得为它单开一个定时器。
     *
     * Without this the map grows for as long as the process lives — it accumulates one
     * entry per distinct shot. Swept opportunistically on write; not worth a timer.
     */
    private void prunePrintedLocked() {
        long now = System.currentTimeMillis();
        printedAt.entrySet().removeIf(e -> (now - e.getValue()) > DEDUPE_WINDOW_MS);
    }

    /**
     * 待打印任务超时(毫秒)/ the pending-print timeout, in milliseconds.
     *
     * 配置来自 SharedPreferences(界面打印设置卡片上的「队列超时」)。坏值退回
     * 默认 —— 队列不该因为一个手滑的值而永远不清。
     *
     * The value comes from SharedPreferences (the "queue timeout" control on the
     * print settings card). A bad value falls back to the default: the queue must
     * not stop expiring because of one typo.
     */
    private long queueTimeoutMs() {
        int minutes = ctx.getSharedPreferences(AppServer.PREFS, Context.MODE_PRIVATE)
                .getInt(AppServer.KEY_QUEUE_TIMEOUT_MIN, AppServer.QUEUE_TIMEOUT_MIN_DEFAULT);
        if (minutes < 1) minutes = AppServer.QUEUE_TIMEOUT_MIN_DEFAULT;
        if (minutes > 1440) minutes = 1440;
        return minutes * 60_000L;
    }

    /**
     * 清掉超时的待打印任务 / drop pending jobs that have outlived the timeout.
     *
     * 为什么要真的删掉而不是只在返回时过滤:队列的含义是「还没打的东西」。过期
     * 任务留着既占 MAX_PENDING 名额,也会挡掉同内容的新上传(按内容去重看的是
     * 整个队列)。删掉只影响补打,shot 历史仍在。
     *
     * Why actually remove them instead of filtering on the way out: the queue means
     * "not printed yet". Expired jobs would keep occupying MAX_PENDING slots and
     * block a fresh upload of the same content (content de-duplication looks at the
     * whole queue). Only the catch-up print is lost; the shot history stays.
     */
    private void pruneExpiredLocked(long now) {
        long timeout = queueTimeoutMs();
        int dropped = 0;
        Iterator<JSONObject> it = pending.iterator();
        while (it.hasNext()) {
            JSONObject o = it.next();
            // 正在后台打印的任务不能被超时清掉 —— 它已经有人接手了。
            // A job being printed in the background must not expire; it is already claimed.
            if (backgroundClaims.contains(o.optString("filename"))) continue;
            long queuedAt = o.optLong("queued_at", 0L);
            // 没有时间戳的任务无法定龄,保守地留着。
            // No timestamp means no age, so keep it rather than guess.
            if (queuedAt > 0L && now - queuedAt >= timeout) {
                it.remove();
                dropped++;
            }
        }
        if (dropped > 0) {
            Log.i(TAG, "队列超时,移除 " + dropped + " 个任务 / dropped " + dropped + " expired job(s)");
        }
    }

    /**
     * 入队 / queue one job.
     *
     * machineId 跟着任务走:它不在 shot 文件里(那份是上传的原始 JSON),只在索引
     * 里,所以打印的那一端只能从这里拿到。少了它,票上印的是 UNKNOWN,而界面上
     * 显示的却是 de1xl。
     *
     * The machine id rides along on the job: it is not in the shot file (that is the
     * uploaded JSON verbatim) and lives only in the index, so the printing end can only
     * get it from here. Without it the receipt says UNKNOWN while the UI says de1xl.
     */
    private void queuePrintLocked(String filename, String machineId) {
        try {
            // 顺手清一遍过期任务:不这么做的话,一条没人取走的旧任务会一直挡着
            // 同内容的新上传(下面的按内容去重看的是整个队列)。
            //
            // Sweep expired jobs first: otherwise one abandoned job keeps blocking
            // a fresh upload of the same content (the de-duplication below looks at
            // the whole queue).
            pruneExpiredLocked(System.currentTimeMillis());

            // 哈希在入队时算一次,存进任务里。之后判断「这份打过没有」就只是查表 ——
            // 否则每次轮询队列都要把 50 个文件重新读一遍再算一遍 SHA-256。
            // 文件写入后不再改变(重复上传是**新文件名**),所以算一次就够了。
            //
            // Hashed once, at queue time, and kept on the job. Afterwards "was this
            // printed?" is a lookup — otherwise every queue poll would re-read and
            // re-hash up to 50 files. A stored file never changes (a re-upload is a
            // *new* filename), so one pass is enough.
            String hash = contentHash(filename);

            // 同一份内容刚打过就别再排队了 —— 上游重复上传的那几次在这里被吸收掉。
            //
            // Identical content that was just printed is not queued at all: this is
            // where the upstream's repeated uploads get absorbed.
            if (hashPrintedRecentlyLocked(hash) || hashPendingLocked(hash)) {
                Log.i(TAG, "内容相同,跳过 / same content, skipping: " + filename);
                return;
            }

            JSONObject job = new JSONObject();
            job.put("filename", filename);
            job.put("hash", hash == null ? "" : hash);
            job.put("machine_id", machineId == null ? "" : machineId);
            job.put("queued", new SimpleDateFormat("HH:mm:ss", Locale.US).format(new Date()));
            // 入队时刻(epoch 毫秒),超时清理按它算年龄。
            // When the job joined the queue (epoch ms); expiry is measured from it.
            job.put("queued_at", System.currentTimeMillis());
            job.put("attempts", 0);
            pending.add(job);
            while (pending.size() > MAX_PENDING) {
                JSONObject evicted = pending.remove(0);
                backgroundClaims.remove(evicted.optString("filename"));
            }
        } catch (Exception ignored) { }
    }

    /** 手动把一条 shot 加进待打印队列 / queue a shot for printing by hand. */
    public void queuePrint(String filename) {
        synchronized (lock) {
            if (!pendingContains(filename)) queuePrintLocked(filename, machineIdOf(filename));
        }
    }

    /**
     * 从索引里查这条 shot 的机器名 / look the machine name up in the index.
     *
     * 手动打印那条路手里只有一个文件名(自动那条是存盘时顺手带上的),所以要回索引
     * 里查一次。查不到就返回空串,票上会显示 UNKNOWN —— 与「本来就没有机器名」是
     * 同一种表现,可以接受。
     *
     * The manual path only has a filename (the automatic one carries the id along from
     * save time), so it looks it up. An empty string when there is nothing to find; the
     * receipt then says UNKNOWN, which is the same as "there was never a machine name".
     *
     * public 也是给 /api/shot 用的:那条路要把机器名放进返回给前端的 JSON(缩略图
     * 和大图也读它),见 AppServer 里的注释。
     *
     * Public for /api/shot as well: that path puts the machine name into the JSON the
     * front end gets back (thumbnails and the large view read it too), see the comment
     * in AppServer.
     */
    public String machineIdOf(String filename) {
        for (JSONObject m : index) {
            if (filename.equals(m.optString("filename"))) return m.optString("machine_id", "");
        }
        return "";
    }

    /**
     * 认领一条任务给后台打印 / claim a pending job for the background path.
     *
     * 认领之后这条任务对前端不可见(见 pendingJobs),直到成功 ack 或失败释放。
     * 这样 App 在后台打印途中被切回前台时,前端不会把同一条再打一遍。
     *
     * After a claim the job is invisible to the front end (see pendingJobs) until it is
     * acked or released, so bringing the app back mid-print cannot make the foreground
     * pump print it again.
     *
     * @return 是否由本次调用认领成功 / whether this call took the claim
     */
    public boolean claimForBackgroundPrint(String filename) {
        synchronized (lock) {
            pruneExpiredLocked(System.currentTimeMillis());
            for (JSONObject o : pending) {
                if (filename.equals(o.optString("filename"))) {
                    return backgroundClaims.add(filename);
                }
            }
            return false;
        }
    }

    /** 释放认领,任务回到前端可见队列 / release a claim so the front end can see it again. */
    public void releaseBackgroundClaim(String filename) {
        synchronized (lock) {
            backgroundClaims.remove(filename);
        }
    }

    /** 这条任务还在队列里吗 / is this job still pending? */
    public boolean isPending(String filename) {
        synchronized (lock) {
            return pendingContains(filename);
        }
    }

    public JSONArray pendingJobs() {
        synchronized (lock) {
            // 前端重新打开时会立刻来取队列 —— 这一步就是「别补打历史」的闸门。
            // The front end polls the moment it comes back; this is the gate that
            // keeps a stale backlog from printing.
            pruneExpiredLocked(System.currentTimeMillis());
            JSONArray arr = new JSONArray();
            for (JSONObject o : pending) {
                // 后台正在打的任务不交给前端 —— 否则切回前台会重复出票。
                // A job owned by the background path is not handed to the front end;
                // otherwise coming back to the foreground prints it twice.
                if (backgroundClaims.contains(o.optString("filename"))) continue;
                // 第一份打成功之后,同内容的其余副本还排在队列里,但它们已经不该再
                // 打了 —— 前端每次轮询都重新拉队列,正是靠这里把它们滤掉。
                //
                // After the first copy prints, the remaining identical copies are still
                // queued but must not print. The front end re-fetches per job, and this
                // filter is what removes them.
                if (hashPrintedRecentlyLocked(o.optString("hash"))) continue;
                arr.put(o);
            }
            return arr;
        }
    }

    private JSONArray pendingFilenames() {
        JSONArray arr = new JSONArray();
        for (JSONObject o : pending) {
            if (!backgroundClaims.contains(o.optString("filename"))) {
                arr.put(o.optString("filename"));
            }
        }
        return arr;
    }

    /**
     * 打印完成回执 / acknowledge a finished print.
     *
     * 失败时保留并累加重试次数,超过 3 次丢弃 —— 一条坏数据不该把队列堵死。
     * On failure the job stays and the attempt count grows; after 3 it is dropped, so
     * one bad record cannot jam the queue.
     */
    public boolean ack(String filename, boolean ok) {
        synchronized (lock) {
            // 不管成功失败,这条任务的认领都结束了。失败时任务可能已经不在队列里,
            // 这里先清掉,免得一个陈旧的 claim 永久挡住同名的重试。
            //
            // Either way the claim is over. The job may already be gone on failure, so
            // clear the claim first; a stale claim would otherwise hide a retry forever.
            backgroundClaims.remove(filename);
            for (int i = 0; i < pending.size(); i++) {
                JSONObject o = pending.get(i);
                if (!filename.equals(o.optString("filename"))) continue;
                if (ok) {
                    // 记下「这份内容刚打过」。同内容的其余副本下一轮就不在队列里了。
                    //
                    // Record that this content has just printed; the remaining
                    // identical copies drop out of the queue on the next poll.
                    String hash = o.optString("hash");
                    if (!hash.isEmpty()) {
                        printedAt.put(hash, System.currentTimeMillis());
                        prunePrintedLocked();
                    }
                    pending.remove(i);
                } else {
                    int attempts = o.optInt("attempts", 0) + 1;
                    if (attempts >= 3) pending.remove(i);
                    else {
                        try { o.put("attempts", attempts); } catch (Exception ignored) { }
                    }
                }
                return true;
            }
            return false;
        }
    }

    public void clearQueue() {
        synchronized (lock) {
            pending.clear();
            backgroundClaims.clear();
        }
    }

    public int shotCount() {
        synchronized (lock) {
            return index.size();
        }
    }
}
