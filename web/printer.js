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
  // 这一块是 Android 上最容易搞错的地方,值得说清楚。
  //
  // 桌面浏览器:页面就是从 Python 服务端拿的,所以相对路径 `/api/status` 正好打回
  // 同一个服务端 —— 什么都不用配。
  //
  // Android APK:Web UI 是**打包在 APK 里**的,WebView 以 http://localhost 加载它。
  // 这时候相对路径 `/api/status` 会打到 WebView 自己身上(asset 目录里没有 /api),
  // 而不是局域网里的服务端。所以必须先知道服务端在哪,把地址补成绝对 URL。
  //
  // 配置存在 localStorage,由首次启动的设置页写入。
  //
  // This is the single easiest thing to get wrong on Android, so it is worth being
  // explicit.
  //
  // Desktop browser: the page itself came from the Python server, so a relative
  // `/api/status` goes right back to that same server — nothing to configure.
  //
  // Android APK: the web UI is **bundled inside the APK** and the WebView loads it
  // from http://localhost. A relative `/api/status` then hits the WebView itself
  // (there is no /api in the asset directory) rather than the server on the LAN.
  // The server address must therefore be known and turned into an absolute URL.
  //
  // The value lives in localStorage and is written by the first-run setup screen.
  var SERVER_KEY = 'pts_server_base';

  /** 把用户输入的地址规整成 "http://host:port" / normalise user input into a base URL. */
  function normalizeBase(input) {
    var u = String(input || '').trim();
    if (!u) return '';
    if (!/^https?:\/\//i.test(u)) u = 'http://' + u;
    return u.replace(/\/+$/, '');
  }

  function getServerBase() {
    try { return normalizeBase(global.localStorage.getItem(SERVER_KEY)); }
    catch (e) { return ''; }
  }

  function setServerBase(input) {
    var u = normalizeBase(input);
    try {
      if (u) global.localStorage.setItem(SERVER_KEY, u);
      else global.localStorage.removeItem(SERVER_KEY);
    } catch (e) { /* 隐私模式下写不进去 / localStorage can be unavailable */ }
    return u;
  }

  /**
   * 需要配置服务端地址吗 / does the server address need configuring.
   * 只有原生端需要;浏览器里永远不需要。
   * Only on native; never in a browser.
   */
  function needsServerConfig() {
    return isNative() && !getServerBase();
  }

  /**
   * 把 API 路径解析成可以直接 fetch 的 URL。
   * Resolve an API path into a URL that can be fetched directly.
   */
  function apiUrl(path) {
    if (!isNative()) return path;      // 浏览器:同源,直接用 / same origin, use as-is
    var base = getServerBase();
    if (!base) return path;            // 还没配;调用方应先调 needsServerConfig()
    return base + path;
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
      machineId: options.machineId || '',
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

  async function pumpQueue() {
    if (!autoPrintEnabled) return;
    var jobs;
    try {
      var r = await fetch(apiUrl('/api/print-queue'));
      var j = await r.json();
      jobs = j.jobs || [];
    } catch (e) {
      return;
    }
    if (!jobs.length) return;

    for (var i = 0; i < jobs.length; i++) {
      var job = jobs[i];
      var result = await printShot(job.filename, {});
      try {
        await fetch(apiUrl('/api/print-queue/ack'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ filename: job.filename, ok: !!result.success })
        });
      } catch (e) { /* 下一轮会重试 / retried next tick */ }
      if (typeof global.onAutoPrint === 'function') {
        try { global.onAutoPrint(job.filename, result); } catch (e) { }
      }
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
    setServerBase: setServerBase,
    needsServerConfig: needsServerConfig,
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
