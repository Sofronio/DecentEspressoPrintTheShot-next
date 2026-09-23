/**
 * PrintTheShot — 前端打印调度 / Front-end print dispatch
 * ======================================================
 *
 * 中文
 * ----
 * 这是「绘制」和「打印」在本项目的接缝处。render.js 只管画,这个模块负责:
 *
 *   1. 选一条路径 —— Mac/Linux/Windows 浏览器里走 HTTP(`POST /api/print`),
 *      Android WebView 里走原生蓝牙桥(Capacitor 插件)。
 *   2. 把画布转成打印机要的字节。
 *   3. 处理「上传到达 → 自动打印」这条队列。
 *
 * 关键设计:ESC/POS 的字节在 JS 里拼好,Android 原生层只负责把字节原样写进蓝牙
 * socket。原生层不碰协议,协议只有一份实现(这份),所以 Mac 和 Android 打出来
 * 的东西必然一致 —— 不会出现「改了协议忘了同步改 Java」这种事。
 *
 * English
 * -------
 * This is where rendering meets printing. render.js only draws; this module:
 *
 *   1. Picks a path — HTTP (`POST /api/print`) in a browser on macOS/Linux/Windows,
 *      the native Bluetooth bridge (a Capacitor plugin) inside an Android WebView.
 *   2. Turns a canvas into the bytes the printer wants.
 *   3. Drives the "upload arrived → print automatically" queue.
 *
 * The important decision: the ESC/POS bytes are assembled in JS, and the Android
 * native layer only writes them to the Bluetooth socket untouched. The protocol
 * lives in exactly one implementation — this one — so macOS and Android cannot
 * drift apart, which is exactly what happens when a protocol is reimplemented in
 * Java and someone forgets to keep the two in step.
 */

(function (global) {
  'use strict';

  var R = global.PrintTheShotRender;

  // =========================================================================
  // ESC/POS 指令 / ESC/POS commands
  // =========================================================================
  // 与 printers/escpos.py 里那份实现逐字节对应 / byte-for-byte the same as printers/escpos.py
  var ESCPOS = {
    GS: 0x1D,
    ESC: 0x1B,

    /** ESC @ — 复位 / reset */
    init: function () { return [0x1B, 0x40]; },

    /** ESC d n — 走纸 / feed n lines */
    feed: function (lines) {
      return [0x1B, 0x64, Math.max(0, Math.min(255, lines | 0))];
    },

    /** GS V — 部分切纸 / partial cut */
    cut: function () { return [0x1D, 0x56, 0x42, 0x00]; },

    /**
     * GS v 0 m xL xH yL yH d1...dk — 光栅位图 / raster bitmap
     *
     * xL/xH 是宽度,单位**字节**;yL/yH 是高度,单位**点**。
     * xL/xH are the width in BYTES; yL/yH are the height in DOTS.
     */
    raster: function (bitmap) {
      var wBytes = bitmap.bytesPerRow;
      var h = bitmap.height;
      var header = [
        ESCPOS.GS, 0x76, 0x30, 0x00,
        wBytes & 0xFF, (wBytes >> 8) & 0xFF,
        h & 0xFF, (h >> 8) & 0xFF
      ];
      var out = new Uint8Array(header.length + bitmap.data.length);
      out.set(header, 0);
      out.set(bitmap.data, header.length);
      return out;
    }
  };

  /** 拼一份完整打印任务:复位 → 位图 → 走纸 → 切纸 / assemble a full print job. */
  function buildJob(bitmap, options) {
    options = options || {};
    var doCut = options.cut !== false;
    var feedLines = typeof options.feedLines === 'number' ? options.feedLines : 3;

    var raster = ESCPOS.raster(bitmap);
    var feed = ESCPOS.feed(feedLines);
    var cut = ESCPOS.cut();
    var init = ESCPOS.init();

    var total = init.length + raster.length + feed.length + (doCut ? cut.length : 0);
    var out = new Uint8Array(total);
    var off = 0;
    out.set(init, off); off += init.length;
    out.set(raster, off); off += raster.length;
    out.set(feed, off); off += feed.length;
    if (doCut) { out.set(cut, off); off += cut.length; }
    return out;
  }

  function bytesToBase64(bytes) {
    var chunk = 0x8000;
    var parts = [];
    for (var i = 0; i < bytes.length; i += chunk) {
      parts.push(String.fromCharCode.apply(null, bytes.subarray(i, i + chunk)));
    }
    return btoa(parts.join(''));
  }

  // =========================================================================
  // 运行环境探测 / Environment detection
  // =========================================================================

  /** 是否跑在 Capacitor 里 / whether we are inside Capacitor. */
  function isNative() {
    return !!(global.Capacitor && global.Capacitor.isNativePlatform &&
              global.Capacitor.isNativePlatform());
  }

  /** 取原生打印插件(没有就返回 null)/ the native printing plugin, or null. */
  function nativePlugin() {
    if (!isNative() || !global.Capacitor || !global.Capacitor.Plugins) return null;
    return global.Capacitor.Plugins.PrintTheShotPrinter || null;
  }

  function transport() {
    return nativePlugin() ? 'native-bluetooth' : 'http';
  }

  // =========================================================================
  // 服务端地址 / server address
  // =========================================================================
  // 桌面浏览器:页面本身就是服务端发的,同源,相对路径即可 —— 什么都不用配。
  //
  // Android:**平板自己就是服务端**(App 内跑着一个 HTTP 服务,见
  // android/app/src/main/java/com/printtheshot/server/)。所以这里固定指向
  // 本机 8000,没有任何需要用户配置的东西。
  //
  // 之前这里是「填一个桌面服务端的地址」的客户端模式,已经去掉 —— 平板不再需要
  // 依附于电脑,它自己就是一个完整的打印节点。
  //
  // Desktop browser: the page came from the server, so same-origin and a relative
  // path is all that is needed — nothing to configure.
  //
  // Android: **the tablet is its own server** (an HTTP service runs inside the app,
  // see android/app/src/main/java/com/printtheshot/server/). So this points at
  // localhost:8000 and there is nothing for the user to configure.
  //
  // This used to be a client mode with a field for a desktop server's address. That
  // is gone: the tablet no longer depends on a computer, it is a complete printing
  // node by itself.
  var ANDROID_SELF_BASE = 'http://localhost:8000';

  function getServerBase() {
    return isNative() ? ANDROID_SELF_BASE : '';
  }

  /** 把 API 路径解析成可以直接 fetch 的 URL。 */
  /** Resolve an API path into a URL that can be fetched directly. */
  function apiUrl(path) {
    if (!isNative()) return path;      // 浏览器:同源,直接用 / same origin, use as-is
    return ANDROID_SELF_BASE + path;
  }

  // =========================================================================
  // 打印配置 / Print configuration
  // =========================================================================
  var configCache = null;

  async function getConfig(force) {
    if (configCache && !force) return configCache;
    try {
      var r = await fetch(apiUrl('/api/print/config'));
      configCache = await r.json();
    } catch (e) {
      configCache = { platform: 'unknown', available: false, print_width: 576, threshold: 200, rotate: true };
    }
    return configCache;
  }

  async function saveConfig(patch) {
    var r = await fetch(apiUrl('/api/print/config'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch || {})
    });
    configCache = null;
    return r.json();
  }

  // =========================================================================
  // Shot 数据缓存 / Shot data cache
  // =========================================================================
  // 缩略图、大图、打印都要同一份 JSON,没必要重复请求三次。
  // Thumbnails, the large view and printing all want the same JSON; no reason to
  // fetch it three times.
  var shotCache = {};          // filename -> {lang: shotData}
  var inflight = {};           // filename -> Promise

  function cacheKey(filename, lang) { return filename + '|' + (lang || 'zh'); }

  async function loadShot(filename, lang) {
    lang = lang || 'zh';
    // 缓存键带上服务端地址:换了服务器还吃旧数据是最难查的一类问题
    // The cache key carries the server address: serving stale data after switching
    // servers is a genuinely hard bug to track down
    var key = cacheKey(filename, lang) + "@" + (getServerBase() || "self");
    if (shotCache[key]) return shotCache[key];
    if (inflight[key]) return inflight[key];

    inflight[key] = fetch(apiUrl('/api/shot?file=' + encodeURIComponent(filename) +
                          '&lang=' + encodeURIComponent(lang)))
      .then(function (r) { return r.json(); })
      .then(function (j) {
        delete inflight[key];
        if (!j || !j.success) throw new Error((j && j.message) || 'load failed');
        shotCache[key] = j.shot;
        return j.shot;
      })
      .catch(function (e) { delete inflight[key]; throw e; });
    return inflight[key];
  }

  function clearShotCache(filename) {
    Object.keys(shotCache).forEach(function (k) {
      if (!filename || k.indexOf(filename + '|') === 0) delete shotCache[k];
    });
  }

  // =========================================================================
  // 绘制到位图 / Render to bitmap
  // =========================================================================
  var offscreen = null;

  function getOffscreen() {
    if (!offscreen) offscreen = document.createElement('canvas');
    return offscreen;
  }

  /**
   * 把一条 shot 画出来并转成 1-bit 位图。
   * Draw a shot and turn it into a 1-bit bitmap.
   *
   * 展示和打印共用同一份绘制代码 —— 打印出来的就是屏幕上看到的那张图,
   * 不存在「预览和实际输出不一样」的问题。
   * Display and printing share one renderer, so what prints is exactly what was
   * on screen; there is no preview-versus-output mismatch to worry about.
   */
  async function shotToBitmap(filename, options) {
    options = options || {};
    var cfg = await getConfig();
    var lang = options.lang || 'zh';
    var shot = await loadShot(filename, lang);

    var canvas = getOffscreen();
    var res = R.renderShotToCanvas(shot, canvas, {
      lang: lang,
      // 机器名:调用方给了就用,否则看 shot 自己带没带。**shot 文件里通常没有** ——
      // 那个值存在服务端的索引里,不在上传的 JSON 里,所以自动打印那条路必须由
      // 队列任务带过来(见 pumpQueue)。少这一句,打出来的就是 UNKNOWN,而屏幕上
      // 显示的却是 de1xl —— 同一份数据两处不一致,最难查。
      //
      // Machine name: use what the caller passed, else whatever the shot carries.
      // The shot file usually has **nothing** — the value lives in the server's index,
      // not in the uploaded JSON — so the auto-print path has to carry it on the queue
      // job (see pumpQueue). Without it the receipt says UNKNOWN while the screen says
      // de1xl: the same data disagreeing with itself, which is the worst kind to trace.
      machineId: options.machineId || shot.machine_id || '',
      strings: options.strings || null,
      scale: 1
    });
    if (!res.ok) throw new Error('render failed: ' + res.error);

    // 等待字体就绪,否则量出来的宽度是回退字体的,排版会跟屏幕不一致
    // Wait for the font, otherwise measurements use a fallback and the layout
    // will not match what was on screen.
    await R.ensureFontReady();

    return R.canvasToBitmap(canvas, {
      targetWidth: options.targetWidth || cfg.print_width || 576,
      rotate: options.rotate !== undefined ? options.rotate : (cfg.rotate !== false),
      threshold: options.threshold || cfg.threshold || 200
    });
  }

  // =========================================================================
  // 打印 / Printing
  // =========================================================================

  /**
   * 打印一条 shot。自动选择 HTTP 或原生蓝牙路径。
   * Print a shot, automatically choosing HTTP or the native Bluetooth path.
   *
   * @returns {{success:boolean, message:string, via:string}}
   */
  async function printShot(filename, options) {
    options = options || {};
    var bitmap;
    try {
      bitmap = await shotToBitmap(filename, options);
    } catch (e) {
      return { success: false, message: String(e.message || e), via: 'render' };
    }

    var plugin = nativePlugin();
    if (plugin) {
      return printViaNative(plugin, bitmap, filename, options);
    }
    return printViaHttp(bitmap, filename, options);
  }

  async function printViaHttp(bitmap, filename, options) {
    var cfg = await getConfig();
    var body = {
      bitmap: R.bitmapToBase64(bitmap),
      width: bitmap.width,
      height: bitmap.height,
      printer: options.printer || cfg.printer || 'default',
      label: filename
    };
    if (options.mode || cfg.mode) body.mode = options.mode || cfg.mode;

    try {
      var r = await fetch(apiUrl('/api/print'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      var j = await r.json();
      return {
        success: !!j.success,
        message: j.message || j.error || '',
        via: 'http',
        width: bitmap.width,
        height: bitmap.height
      };
    } catch (e) {
      return { success: false, message: String(e), via: 'http' };
    }
  }

  async function printViaNative(plugin, bitmap, filename, options) {
    try {
      var cfg = await getConfig();
      // 原生层只负责把这段字节写进蓝牙 —— 协议全在这里拼好
      // The native layer just writes these bytes to Bluetooth; the protocol is
      // assembled here, in full.
      var job = buildJob(bitmap, {
        feedLines: options.feedLines !== undefined ? options.feedLines : (cfg.feed_lines || 3),
        cut: options.cut !== undefined ? options.cut : (cfg.cut !== false)
      });

      // 没显式指定打印机时,用设置页里记住的那台。地址存在同一个键上,
      // 免得两个模块各存一份、哪天对不上。
      // With no explicit printer, use the one remembered on the settings screen. Both
      // modules read the same key so they cannot drift apart.
      var remembered = '';
      try { remembered = global.localStorage.getItem('pts_bt_printer') || ''; } catch (e) { }
      var r = await plugin.printRaw({
        data: bytesToBase64(job),
        address: options.printer || remembered || cfg.printer || ''
      });
      return {
        success: !!(r && r.success),
        message: (r && r.message) || '',
        via: 'native-bluetooth',
        width: bitmap.width,
        height: bitmap.height
      };
    } catch (e) {
      return { success: false, message: String(e), via: 'native-bluetooth' };
    }
  }

  /** 枚举打印机(两种路径都支持)/ enumerate printers on either path. */
  async function listPrinters() {
    var plugin = nativePlugin();
    if (plugin) {
      try {
        if (plugin.requestPermissions) await plugin.requestPermissions();
        var r = await plugin.listPrinters();
        return { platform: 'android', printers: (r && r.printers) || [], available: true };
      } catch (e) {
        return { platform: 'android', printers: [], available: false, error: String(e) };
      }
    }
    try {
      var res = await fetch(apiUrl('/api/printers'));
      return await res.json();
    } catch (e) {
      return { platform: 'unknown', printers: [], available: false, error: String(e) };
    }
  }

  // =========================================================================
  // 自动打印队列 / Auto-print queue
  // =========================================================================
  // 上传到达时服务端只把它挂进队列(它手里没有图),由这一端来取走、渲染、打印,
  // 打完再回执。这样「谁有打印机谁负责渲染」这条边界就清楚了。
  //
  // On upload the server only queues the job — it has no image. This end claims
  // it, renders, prints, and acknowledges. That keeps the boundary clean: the end
  // that owns the printer is the end that renders.
  var autoTimer = null;
  var autoPrintEnabled = true;
  var pumping = false;

  /**
   * 本端已经成功打印过的文件名 → 时刻 / filenames this end has already printed.
   *
   * 为什么需要它:打印成功之后要靠 ack 让服务端把任务摘掉,而 ack 是会失败的
   * (网络抖一下、服务端正忙)。ack 没送到,任务就还留在队列里,下一轮又会取到
   * 同一个文件 —— **同一张票一张接一张地打**。实测踩过这个:打印机疯狂吐纸。
   *
   * 所以「同一个文件只打一次」必须由这一端自己保证,不能依赖 ack 一定成功。
   * 服务端那份按**内容**去重管的是另一件事(同一 shot 被上传多次、文件名不同),
   * 两者互补,缺一不可。
   *
   * Why this exists: after a successful print the ack is what makes the server drop the
   * job, and an ack can fail — a network blip, a busy server. When it does, the job
   * stays queued and the next pass picks up the same file again, printing the same
   * receipt over and over. This was hit for real: the printer spat paper continuously.
   *
   * So "print each file once" has to be this end's own invariant, not something that
   * depends on the ack arriving. The server's content-based de-duplication covers a
   * different case — one shot uploaded several times under different filenames — and the
   * two are complementary.
   */
  var printedFiles = {};

  function markPrinted(filename) {
    var now = Date.now();
    printedFiles[filename] = now;
    // 长跑之后不能让它一直涨。10 分钟足够覆盖任何合理的重试,之后同名文件不可能
    // 还在队列里(文件名带微秒 ID,不会重复)。
    //
    // Bounded so it cannot grow forever. Ten minutes covers any plausible retry, and a
    // filename cannot come back later — they carry a microsecond ID.
    var cutoff = now - 10 * 60 * 1000;
    Object.keys(printedFiles).forEach(function (f) {
      if (printedFiles[f] < cutoff) delete printedFiles[f];
    });
  }

  async function fetchQueue() {
    var r = await fetch(apiUrl('/api/print-queue'));
    var j = await r.json();
    return j.jobs || [];
  }

  /** 回执一个任务 / acknowledge one job. 失败不抛,由调用方决定怎么办。 */
  async function ackJob(filename, ok) {
    try {
      await fetch(apiUrl('/api/print-queue/ack'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: filename, ok: !!ok })
      });
      return true;
    } catch (e) {
      return false;
    }
  }

  async function pumpQueue() {
    if (!autoPrintEnabled) return;

    // 重入保护。服务端的唤醒(收到 shot 时推一把)和 5 秒定时器可能同时进来,
    // 两边都跑的话同一个任务会被取到两次、打印两次 —— 而重复打印正是这个函数
    // 要防的事,所以先挡住重入。
    //
    // Re-entry guard. The server's wake (a poke when a shot arrives) and the 5-second
    // timer can land at the same moment; running both would claim the same job twice
    // and print it twice — and duplicate printing is exactly what this function
    // exists to prevent, so re-entry is blocked first.
    if (pumping) return;
    pumping = true;
    try {
      // 每打一个任务就重新拉一次队列,而不是一次抓一批。服务端在每次成功 ack 之后
      // 会把**同内容**的其余副本一并摘掉,只有重新拉取才能让这个效果立刻生效;
      // 一次抓一批的话,同一 shot 的重复上传会在同一轮里被逐个打光。
      //
      // Re-fetch the queue for each job instead of draining a batch. After each
      // successful ack the server also drops the remaining copies of *the same
      // content*, and only a fresh fetch sees that; with a batch, the duplicate
      // uploads of one shot all print in the same pass.
      // 一轮最多打这么多个。正常情况队列很短,碰到这个数说明前面的假设破了 ——
      // 宁可停下来留个日志,也不要在这里转圈。
      //
      // A hard cap per pass. A normal queue is short; hitting this means an assumption
      // broke. Better to stop with a log line than to keep spinning here.
      var MAX_PER_PASS = 20;
      var done = 0;

      while (autoPrintEnabled && done < MAX_PER_PASS) {
        var jobs;
        try {
          jobs = await fetchQueue();
        } catch (e) {
          return;
        }
        if (!jobs.length) return;

        // 找第一个本端还没打过的任务。队头可能是「刚打过、但 ack 没生效」的那个 ——
        // 对它就补一次回执,别让它把队头堵死,更不要再打一遍。
        //
        // Take the first job this end has not printed yet. The head may be one that just
        // printed but whose ack never landed: re-ack it and move on — never print it
        // again, and never let it jam the queue.
        var job = null;
        for (var i = 0; i < jobs.length; i++) {
          if (printedFiles[jobs[i].filename]) {
            ackJob(jobs[i].filename, true);
            continue;
          }
          job = jobs[i];
          break;
        }
        if (!job) {
          // 整个队列都是打过的,说明 ack 一直没生效。退出去等下一轮重试,
          // 总比把同一张票再打一遍强。
          //
          // Everything queued has been printed, so the acks are not landing. Leave it
          // for the next pass — better than printing the same receipt again.
          return;
        }

        // 机器名跟着任务走。它不在 shot 文件里(那是上传的原样 JSON),只在服务端
        // 索引里 —— 所以队列任务要把它带上,否则自动打印出来的票永远是 UNKNOWN。
        //
        // The machine name travels with the job. It is not in the shot file (that is the
        // uploaded JSON verbatim) and lives only in the server's index, so the queue job
        // has to carry it — otherwise every auto-printed receipt says UNKNOWN.
        var result = await printShot(job.filename, { machineId: job.machine_id || '' });
        done++;

        // 打印成功就先记上,**再**去 ack。顺序很重要:ack 失败时这一笔仍然留着,
        // 于是下一轮不会再打它。
        //
        // Record the success *before* acking. The order matters: if the ack fails, the
        // record is already there and the next pass will not print it again.
        if (result.success) markPrinted(job.filename);

        await ackJob(job.filename, result.success);

        if (typeof global.onAutoPrint === 'function') {
          try { global.onAutoPrint(job.filename, result); } catch (e) { }
        }

        // 失败的任务仍在队列最前面(服务端保留它并累加 attempts,3 次后丢弃)。
        // 这里必须退出去等下一轮,否则这个 while 会立刻又抓到同一个任务,把 3 次
        // 机会在一瞬间烧光。
        //
        // A failed job stays at the head of the queue — the server keeps it and counts
        // attempts, dropping it after 3. Bail out and wait for the next tick, or this
        // loop would immediately claim it again and burn all 3 attempts at once.
        if (!result.success) return;
      }
    } finally {
      pumping = false;
    }
  }

  function startAutoPrint(intervalMs) {
    if (autoTimer) return;
    autoTimer = setInterval(pumpQueue, intervalMs || 5000);
    pumpQueue();
  }

  function stopAutoPrint() {
    if (autoTimer) { clearInterval(autoTimer); autoTimer = null; }
  }

  function setAutoPrint(on) {
    autoPrintEnabled = !!on;
    if (autoPrintEnabled) pumpQueue();
  }

  // =========================================================================
  // 导出 / Exports
  // =========================================================================
  var api = {
    ESCPOS: ESCPOS,
    buildJob: buildJob,
    bytesToBase64: bytesToBase64,
    isNative: isNative,
    transport: transport,
    apiUrl: apiUrl,
    getServerBase: getServerBase,
    getConfig: getConfig,
    saveConfig: saveConfig,
    loadShot: loadShot,
    clearShotCache: clearShotCache,
    shotToBitmap: shotToBitmap,
    printShot: printShot,
    listPrinters: listPrinters,
    pumpQueue: pumpQueue,
    startAutoPrint: startAutoPrint,
    stopAutoPrint: stopAutoPrint,
    setAutoPrint: setAutoPrint
  };

  global.PrintTheShotPrinter = api;
})(typeof window !== 'undefined' ? window : globalThis);
