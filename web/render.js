/**
 * PrintTheShot — Web Canvas 绘制模块 / Web Canvas rendering module
 * ================================================================
 *
 * 中文
 * ----
 * 把原来服务端 Pillow(ImageDraw)的图表绘制逻辑完整翻译到浏览器 Canvas。
 * 服务端不再生成任何 PNG;Mac 与 Android 共用这一份绘制代码,保证两端渲染一致。
 *
 * 对外导出的函数:
 *   PrintTheShotRender.renderShotToCanvas(shotJson, canvas, options)
 *       — 把 shot JSON 画到给定 canvas 上(展示与打印共用)
 *   PrintTheShotRender.canvasToBitmap(canvas, options)
 *       — 把 canvas 转成 1-bit 单色位图数据(供打印)
 *   PrintTheShotRender.mountTestViewer(...)   — 测试页辅助函数
 *
 * 设计约定 / Design notes:
 *   - 全部坐标常量与 Pillow 版逐一对应,数学部分不做任何改动。
 *   - 文字锚点按 Pillow 语义实现(la / rm / lm / mt),不是 Canvas 默认锚点,
 *     这样字号、基线、左右对齐的结果与旧版输出一致。
 *   - 不依赖任何第三方库,可在浏览器 / Android WebView / Capacitor 里直接跑。
 *
 * English
 * -------
 * A complete translation of the former server-side Pillow (ImageDraw) chart
 * logic into browser Canvas. The server no longer produces any PNG; macOS and
 * Android share this single renderer so both platforms draw identically.
 *
 * Exports:
 *   PrintTheShotRender.renderShotToCanvas(shotJson, canvas, options)
 *       — draw a shot JSON onto a canvas (shared by display and printing)
 *   PrintTheShotRender.canvasToBitmap(canvas, options)
 *       — convert a canvas into 1-bit monochrome bitmap data (for printing)
 *   PrintTheShotRender.mountTestViewer(...)   — helper for the test page
 *
 * Design notes:
 *   - Every geometry constant maps 1:1 to the Pillow version; no maths changed.
 *   - Text anchors follow Pillow semantics (la / rm / lm / mt) rather than the
 *     Canvas defaults, so sizes, baselines and alignment match the old output.
 *   - Zero third-party dependencies; runs in a browser, an Android WebView or
 *     a Capacitor container as-is.
 */

(function (global) {
  'use strict';

  // =========================================================================
  // 几何常量(与 Pillow 版完全一致)/ Geometry constants (identical to Pillow)
  // =========================================================================
  // 画布 1296x576(203dpi 下的 6.38x2.84 英寸)
  // Canvas 1296x576 (6.38 x 2.84 inches at 203dpi)
  var CHART_W = 1296;
  var CHART_H = 576;

  // 左侧预留带:温度标题(0-22)|温度刻度(→46)|温度轴(52)|压力标题(56-78)|压力刻度(→94)|压力轴(98)|绘图区(104-850)
  var AX_TEMP_X = 62;    // 温度轴(最左) / temperature axis (leftmost)
  var AX_PRES_X = 112;   // 压力轴(绘图 y 轴) / pressure axis (plot y-axis)
  var AX_FLOW_X = 854;   // 流速轴(最右) / flow axis (rightmost)
  var PLOT_L = 112;      // 绘图区左缘=压力轴 / plot left edge = pressure axis
  var PLOT_R = 850;
  var PLOT_T = 58;
  var PLOT_B = 440;
  var COL1_X = 905, COL1_MAXW = 170;   // 第一列文本(冲煮信息) / column 1 (brew info)
  var COL2_X = 1085, COL2_MAXW = 211;  // 第二列文本(豆子/方案信息) / column 2 (bean/profile)
  var LINE_H = 26;                      // 文本行距 / line height
  var LEGEND_Y = 474;

  // 打印用位图宽度(点数)。80mm 热敏机常见 576 点(203dpi,72mm 可打印宽度)。
  // Bitmap width in dots for printing. 80mm thermal printers are commonly 576
  // dots (203dpi over a 72mm printable width).
  var DEFAULT_PRINT_WIDTH = 576;

  // 二值化阈值:与旧版一致(>200 视为白)/ Binarisation threshold, same as before
  var DEFAULT_THRESHOLD = 200;

  // 字体栈:优先使用项目自带的 Noto Sans CJK SC,保证 Mac / Android 渲染一致
  // Font stack: prefer the bundled Noto Sans CJK SC so macOS and Android agree
  var FONT_STACK =
    '"NotoSansCJKsc", "Noto Sans CJK SC", "Noto Sans SC", ' +
    '"PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", ' +
    '"Droid Sans Fallback", sans-serif';

  // 字体是否已就绪 / whether the bundled webfont has finished loading
  var fontReady = false;
  var fontReadyPromise = null;

  // =========================================================================
  // 语言文案 / UI strings
  // =========================================================================
  // 绘制时用到的全部图面文案。默认内置中英两套;服务端可以下发额外的语言包
  // (通过 options.strings 覆盖),这样服务端新增语言时前端无需改代码。
  //
  // Every on-chart label. Chinese and English are built in; the server may push
  // extra language packs through options.strings, so adding a language on the
  // server side needs no front-end change.
  var STRINGS = {
    zh: {
      chart_pressure: '压力 (巴)',
      chart_flow: '流速 (克/秒)',
      chart_water_flow: '水流流速',
      chart_coffee_flow: '咖啡流速',
      chart_temperature: '温度 (°C)',
      chart_time: '时间 (秒)',
      chart_date_time: '日期时间',
      chart_profile: '冲煮方案',
      chart_extraction: '萃取参数',
      chart_grinder_temp: '研磨与温度',
      chart_in_weight: '咖啡粉',
      chart_out_weight: '咖啡液',
      chart_shot_time: '时间',
      chart_grind_setting: '研磨度',
      chart_initial_temp: '温度',
      chart_unknown_profile: '未知方案',
      chart_na: '未记录',
      chart_bean_info: '咖啡豆信息',
      chart_profile_info: '冲煮方案信息',
      chart_tasting_note: '品鉴感受',
      machine_id: '机器: '
    },
    en: {
      chart_pressure: 'Pressure (Bar)',
      chart_flow: 'Flow Rate (g/s)',
      chart_water_flow: 'Water Flow',
      chart_coffee_flow: 'Coffee Flow',
      chart_temperature: 'Temp (°C)',
      chart_time: 'Time (s)',
      chart_date_time: 'Date&Time',
      chart_profile: 'Profile',
      chart_extraction: 'Extraction',
      chart_grinder_temp: 'Grind&Temp',
      chart_in_weight: 'In',
      chart_out_weight: 'Out',
      chart_shot_time: 'Time',
      chart_grind_setting: 'Grind',
      chart_initial_temp: 'Temp',
      chart_unknown_profile: 'Unknown Profile',
      chart_na: 'N/A',
      chart_bean_info: 'Bean Info',
      chart_profile_info: 'Profile Info',
      chart_tasting_note: 'Tasting Note',
      machine_id: ''
    }
  };

  /**
   * 取某个语言的图面文案表。内置表为基础,options.strings 里给的键优先。
   * Resolve the label table for a language: built-ins first, then any overrides.
   */
  function resolveStrings(lang, overrides) {
    var base = STRINGS[lang] || STRINGS.en;
    if (!overrides) return base;
    var out = {};
    for (var k in base) if (Object.prototype.hasOwnProperty.call(base, k)) out[k] = base[k];
    for (var k2 in overrides) if (Object.prototype.hasOwnProperty.call(overrides, k2)) out[k2] = overrides[k2];
    return out;
  }

  // =========================================================================
  // 字体与文字度量 / Fonts and text metrics
  // =========================================================================

  /**
   * 让浏览器先加载自带的 Noto Sans CJK SC,避免首帧量到的宽度是回退字体。
   * Make sure the bundled webfont is loaded first, otherwise the first frame
   * measures against a fallback font.
   */
  function ensureFontReady() {
    if (fontReadyPromise) return fontReadyPromise;
    fontReadyPromise = new Promise(function (resolve) {
      if (typeof document === 'undefined' || !document.fonts) {
        fontReady = true;
        resolve(true);
        return;
      }
      // 字体由页面 CSS 的 @font-face 声明;这里主动请求一次并等待就绪
      // The font is declared via @font-face; request it and await readiness
      var probe = '16px ' + FONT_STACK;
      var jobs = [];
      try {
        jobs.push(document.fonts.load('16px "NotoSansCJKsc"', '测试Test123'));
        jobs.push(document.fonts.load('bold 16px "NotoSansCJKsc"', '测试Test123'));
      } catch (e) {
        /* 老浏览器忽略 / older browsers: ignore */
      }
      Promise.all(jobs)
        .catch(function () { /* 字体缺失时回退到系统字体 / fall back to system fonts */ })
        .then(function () { return document.fonts.ready; })
        .catch(function () { })
        .then(function () {
          fontReady = true;
          resolve(true);
        });
    });
    return fontReadyPromise;
  }

  /**
   * 造一个 canvas 用的 font 字符串。px 为逻辑像素字号,与 Pillow 的像素字号一一对应。
   * Build a canvas font string. `px` is the logical pixel size, matching the
   * pixel sizes used by the Pillow version one-to-one.
   */
  function fontOf(px, weight) {
    return (weight || 'normal') + ' ' + px + 'px ' + FONT_STACK;
  }

  /**
   * 取字体在给定字号下的 ascender / descender,用于复刻 Pillow 的文字锚点。
   * Pillow 的锚点语义与 Canvas 不同,必须自己换算才能对齐旧版输出。
   *
   * Read ascender/descender for a font size so Pillow's text anchors can be
   * reproduced exactly — Pillow's anchor semantics differ from Canvas and must
   * be converted by hand to match the old output.
   */
  var _metricsCache = {};
  function fontMetrics(ctx, px, weight) {
    var key = (weight || 'normal') + '|' + px;
    if (_metricsCache[key]) return _metricsCache[key];
    ctx.save();
    ctx.font = fontOf(px, weight);
    var m = ctx.measureText('Hg漢');
    var asc = m.fontBoundingBoxAscent;
    var desc = m.fontBoundingBoxDescent;
    ctx.restore();
    if (typeof asc !== 'number' || typeof desc !== 'number' || !isFinite(asc)) {
      // 回退估算:经验比例 / fallback ratios when the browser lacks font bbox metrics
      asc = px * 0.88;
      desc = px * 0.22;
    }
    var out = { ascent: asc, descent: desc };
    _metricsCache[key] = out;
    return out;
  }

  /**
   * 按 Pillow 的锚点语义摆放文字。
   * Anchor semantics, ported from Pillow:
   *   la = 左 + ascender 顶端(left, ascender top)
   *   lm = 左 + 垂直居中(left, vertical middle)
   *   rm = 右 + 垂直居中(right, vertical middle)
   *   mt = 水平居中 + ascender 顶端(horizontal centre, ascender top)
   * Pillow 的 "m" 取 ascender 与 descender 跨度的中点。
   * Pillow's "m" is the midpoint of the ascender-to-descender span.
   */
  function textAt(ctx, text, x, y, px, anchor, weight) {
    ctx.save();
    ctx.font = fontOf(px, weight);
    var fm = fontMetrics(ctx, px, weight);
    var a = anchor || 'la';
    var hAlign = a.charAt(0);
    var vAlign = a.charAt(1);

    ctx.textAlign = hAlign === 'm' ? 'center' : (hAlign === 'r' ? 'right' : 'left');
    ctx.textBaseline = 'alphabetic';

    var baselineY;
    if (vAlign === 'm') {
      baselineY = y + (fm.ascent - fm.descent) / 2;
    } else {
      // 'a' — y 是 ascender 顶端 / y is the ascender top
      baselineY = y + fm.ascent;
    }
    ctx.fillStyle = '#000';
    ctx.fillText(text, x, baselineY);
    ctx.restore();
    return ctx;
  }

  /**
   * 按宽度量测文字(等价 Pillow 的 draw.textlength / font.getlength)。
   * Measure text width (the equivalent of Pillow's draw.textlength).
   */
  function textWidth(ctx, text, px, weight) {
    ctx.save();
    ctx.font = fontOf(px, weight);
    var w = ctx.measureText(text).width;
    ctx.restore();
    return w;
  }

  /**
   * 竖向文字:旋转 90°,自下而上阅读(等价 Pillow 的 _vtext,同 matplotlib ylabel)。
   * Vertical text rotated 90° reading bottom-to-top (the Pillow _vtext equivalent,
   * matching matplotlib's ylabel behaviour).
   */
  function vText(ctx, x, yCenter, text, px) {
    ctx.save();
    ctx.translate(x, yCenter);
    ctx.rotate(-Math.PI / 2);   // 逆时针 90° / 90° counter-clockwise
    ctx.font = fontOf(px);
    ctx.textAlign = 'left';
    ctx.textBaseline = 'alphabetic';
    var fm = fontMetrics(ctx, px);
    // 旋转后文字沿新的 x 轴(即屏幕向上)绘制,y 方向为文字高度
    // After rotation the text runs along the new x-axis (screen "up"); the y
    // offset is the text height, so centre it on the glyph box.
    ctx.fillStyle = '#000';
    ctx.fillText(text, 0, (fm.ascent - fm.descent) / 2);
    ctx.restore();
  }

  // =========================================================================
  // 线条工具 / Line helpers
  // =========================================================================

  /**
   * 任意方向虚线:沿线段方向参数化步进,支持斜线 / 曲线段。
   * Dashed line at any angle: parameterised stepping along the segment direction,
   * so slanted and curved runs work too.
   */
  function dashLine(ctx, x1, y1, x2, y2, width, pattern) {
    pattern = pattern || [14, 8];
    var dx = x2 - x1, dy = y2 - y1;
    var length = Math.sqrt(dx * dx + dy * dy);
    if (length <= 0) return;
    var ux = dx / length, uy = dy / length;
    var pos = 0, i = 0;
    ctx.save();
    ctx.lineWidth = width || 1;
    ctx.strokeStyle = '#000';
    ctx.lineCap = 'butt';
    while (pos < length) {
      var seg = pattern[i % pattern.length];
      var end = Math.min(pos + seg, length);
      if (i % 2 === 0) {  // 偶数段=画 / even elements are drawn
        ctx.beginPath();
        ctx.moveTo(x1 + ux * pos, y1 + uy * pos);
        ctx.lineTo(x1 + ux * end, y1 + uy * end);
        ctx.stroke();
      }
      pos = end;
      i++;
    }
    ctx.restore();
  }

  /**
   * 沿折线路径连续应用虚线模式(相位跨线段延续,与 matplotlib 一致)。
   * 关键:不能在每条采样线段上重置相位,否则短段几乎都落在「画」区间,虚线看起来像实线。
   *
   * Apply the dash pattern continuously along a polyline (phase carries across
   * segments, matching matplotlib). Resetting the phase per sampled segment
   * would make short segments land almost entirely in the "draw" interval and
   * the dashed line would look solid.
   */
  function drawPathDashed(ctx, pts, pattern, width) {
    width = width || 3;
    if (!pts || pts.length < 2) return;
    var segIdx = 0;     // pattern 元素索引 / pattern element index
    var phase = 0;      // 当前元素已消耗长度 / length consumed in the current element
    ctx.save();
    ctx.lineWidth = width;
    ctx.strokeStyle = '#000';
    ctx.lineCap = 'butt';
    ctx.beginPath();
    for (var k = 0; k < pts.length - 1; k++) {
      var x0 = pts[k][0], y0 = pts[k][1];
      var x1 = pts[k + 1][0], y1 = pts[k + 1][1];
      var dx = x1 - x0, dy = y1 - y0;
      var segLen = Math.sqrt(dx * dx + dy * dy);
      if (segLen <= 0) continue;
      var ux = dx / segLen, uy = dy / segLen;
      var pos = 0;
      while (pos < segLen) {
        var seg = pattern[segIdx % pattern.length];
        var take = Math.min(seg - phase, segLen - pos);
        if (segIdx % 2 === 0) {  // 偶数元素=画 / even elements are drawn
          ctx.moveTo(x0 + ux * pos, y0 + uy * pos);
          ctx.lineTo(x0 + ux * (pos + take), y0 + uy * (pos + take));
        }
        pos += take;
        phase += take;
        if (phase >= seg - 1e-9) {
          phase = 0;
          segIdx++;
        }
      }
    }
    ctx.stroke();
    ctx.restore();
  }

  /**
   * 画曲线:线型沿路径方向绘制、相位连续,与原版 matplotlib 一致。
   * 所有值钳制到 [0, yMax](传感器毛刺会产生负值,必须避免画到图外)。
   *
   * Draw a curve with path-continuous dashes, matching the original matplotlib
   * output. All values are clamped to [0, yMax]: sensor spikes can go negative
   * and must never be drawn outside the plot.
   *
   *   solid   折线 / polyline
   *   dashed  '--'   (17, 8)px @203dpi
   *   dotted  ':'    (2.5, 7)px
   *   dashdot '-.'   (17, 8.5, 2.5, 8.5)px
   */
  function plotCurveStyle(ctx, xs, ys, xScale, yMax, style) {
    var iy = function (v) {
      v = Math.min(Math.max(v, 0), yMax);
      return PLOT_B - (v / yMax) * (PLOT_B - PLOT_T);
    };
    var ix = function (t) { return PLOT_L + t * xScale; };

    var pts = [];
    for (var i = 0; i < xs.length && i < ys.length; i++) pts.push([ix(xs[i]), iy(ys[i])]);
    if (pts.length < 2) return;

    if (style === 'solid') {
      ctx.save();
      ctx.lineWidth = 3;
      ctx.strokeStyle = '#000';
      ctx.lineJoin = 'round';
      ctx.lineCap = 'round';
      ctx.beginPath();
      ctx.moveTo(pts[0][0], pts[0][1]);
      for (var k = 1; k < pts.length; k++) ctx.lineTo(pts[k][0], pts[k][1]);
      ctx.stroke();
      ctx.restore();
    } else {
      var patterns = {
        dashed: [17, 8],
        dotted: [2.5, 7],
        dashdot: [17, 8.5, 2.5, 8.5]
      };
      drawPathDashed(ctx, pts, patterns[style] || patterns.dashed, 3);
    }
  }

  /**
   * 选「漂亮」的刻度步长:从候选步长中挑一个让刻度数 <= maxTicks。
   * Pick a "nice" tick step: the first candidate that keeps the count <= maxTicks.
   */
  function niceTicks(vmin, vmax, maxTicks) {
    maxTicks = maxTicks || 10;
    var span = vmax - vmin;
    if (span <= 0) return [vmin];
    var candidates = [1, 2, 5, 10, 15, 20, 30, 60];
    var step = 1;
    for (var i = 0; i < candidates.length; i++) {
      if (span / candidates[i] <= maxTicks) { step = candidates[i]; break; }
    }
    var out = [];
    for (var v = vmin; v <= vmax + 1e-9; v += step) out.push(v);
    return out;
  }

  // =========================================================================
  // 文本换行与字号自适应 / Text wrapping and font fitting
  // =========================================================================

  /**
   * 按实际渲染宽度换行:全角字符按真实像素宽度计算,西文尽量按空格断行。
   * Wrap by measured render width: full-width CJK counted at its real pixel
   * width, space-aware for Latin text.
   */
  function wrapByWidth(ctx, text, px, maxWidth, maxLines) {
    maxLines = maxLines || 16;
    if (!text) return [];
    var lines = [];
    var raw = String(text).split('\n');
    for (var r = 0; r < raw.length; r++) {
      var line = raw[r];
      if (!line) { lines.push(''); continue; }
      var cur = '';
      var lastSpace = -1;
      for (var i = 0; i < line.length; i++) {
        var ch = line.charAt(i);
        if (/\s/.test(ch)) lastSpace = i;
        if (textWidth(ctx, cur + ch, px) <= maxWidth) {
          cur += ch;
        } else {
          if (lastSpace > 0 && cur.length > lastSpace) {
            lines.push(cur.substring(0, lastSpace).replace(/\s+$/, ''));
            cur = cur.substring(lastSpace).replace(/^\s+/, '') + ch;
            lastSpace = -1;
          } else {
            lines.push(cur);
            cur = ch;
            lastSpace = -1;
          }
        }
      }
      lines.push(cur);
    }
    if (lines.length > maxLines) {
      lines = lines.slice(0, maxLines);
      lines.push('...');
    }
    return lines;
  }

  /**
   * 按文本长度调整字号,保证能放下。阈值 colWidth*2.2 与旧版一致(刻意保守,
   * 让长标题自动降一档字号),下限 12px。
   *
   * Pick a font size that fits the text. The colWidth*2.2 threshold is kept from
   * the previous version on purpose — it is deliberately conservative so long
   * titles drop a size. Minimum is 12px.
   */
  function fitFontSize(ctx, text, baseSize, colWidth) {
    var size = baseSize;
    while (size > 12 && textWidth(ctx, String(text), size) > colWidth * 2.2) size -= 2;
    return size;
  }

  // =========================================================================
  // 数据准备 / Data preparation
  // =========================================================================

  /**
   * 从 raw shot JSON 里抽出绘制需要的序列,并做与旧版一致的清洗:
   *   1. 各行按最短长度对齐(空序列自动忽略,避免旧版空 by_weight 崩溃);
   *   2. by_weight 为空时回退到 by_weight_raw(部分固件写入不同字段);
   *   3. 剔除起点毛刺(首两点跳变超过数据范围一半时丢弃首采样点),
   *      避免 x=0 处出现竖直「速降」线。
   *
   * Extract the series needed for drawing from a raw shot JSON, applying the
   * same cleanup as before:
   *   1. align all series to the shortest length (empty series are ignored,
   *      which is what used to crash on an empty by_weight);
   *   2. fall back to by_weight_raw when by_weight is empty (some firmware
   *      versions write a different field);
   *   3. drop the start glitch — if the first two samples jump by more than half
   *      the data range, drop the first sample so no vertical "plunge" appears
   *      at x=0.
   *
   * 返回 null 表示数据过短,无法绘制。
   * Returns null when there are too few samples to draw.
   */
  function prepareSeries(shotJson) {
    var data = shotJson || {};
    var num = function (arr) {
      if (!arr) return [];
      var out = [];
      for (var i = 0; i < arr.length; i++) {
        var v = parseFloat(arr[i]);
        out.push(isFinite(v) ? v : 0);
      }
      return out;
    };

    var elapsed = num(data.elapsed);
    var pressure = num(data.pressure && data.pressure.pressure);
    var flow = num(data.flow && data.flow.flow);
    var byWeightRaw = (data.flow && (data.flow.by_weight)) || (data.flow && data.flow.by_weight_raw) || [];
    var byWeight = num(byWeightRaw);
    var basketTemp = num(data.temperature && data.temperature.basket);

    var lengths = [elapsed.length, pressure.length, flow.length, basketTemp.length];
    if (byWeight.length) lengths.push(byWeight.length);
    var minLen = Math.min.apply(null, lengths);
    if (minLen < 2) return null;

    elapsed = elapsed.slice(0, minLen);
    pressure = pressure.slice(0, minLen);
    flow = flow.slice(0, minLen);
    basketTemp = basketTemp.slice(0, minLen);
    if (byWeight.length) byWeight = byWeight.slice(0, minLen);
    else byWeight = [];

    // 起点毛刺检测 / start-glitch detection
    var startGlitch = function (vals) {
      if (!vals || vals.length < 2) return false;
      var mn = Math.min.apply(null, vals), mx = Math.max.apply(null, vals);
      var rng = mx - mn;
      return rng > 0 && Math.abs(vals[0] - vals[1]) > 0.5 * rng;
    };
    var glitched = startGlitch(pressure) || startGlitch(flow) ||
      startGlitch(basketTemp) || (byWeight.length && startGlitch(byWeight));
    if (glitched) {
      elapsed = elapsed.slice(1);
      pressure = pressure.slice(1);
      flow = flow.slice(1);
      basketTemp = basketTemp.slice(1);
      if (byWeight.length) byWeight = byWeight.slice(1);
    }

    return {
      elapsed: elapsed,
      pressure: pressure,
      flow: flow,
      byWeight: byWeight,
      basketTemp: basketTemp
    };
  }

  /**
   * 解析时间戳,返回 {date, time} 字符串。优先用 timestamp,回退 date 字段。
   * Parse the timestamp into {date, time}. `timestamp` wins, `date` is the fallback.
   */
  function parseDateTime(data) {
    var out = { date: 'N/A', time: 'N/A' };
    var pad = function (n) { return (n < 10 ? '0' : '') + n; };

    var ts = data && data.timestamp;
    if (ts) {
      var sec = parseFloat(ts);
      if (isFinite(sec) && sec > 0) {
        var d = new Date(sec * 1000);
        if (!isNaN(d.getTime())) {
          out.date = d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
          out.time = pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
          return out;
        }
      }
    }
    // 回退:解析 DE1 的 "Sat Aug 01 09:30:00 2026" 形式
    // Fallback: parse DE1's "Sat Aug 01 09:30:00 2026" form
    var dateStr = data && data.date;
    if (dateStr) {
      var d2 = new Date(dateStr);
      if (!isNaN(d2.getTime())) {
        out.date = d2.getFullYear() + '-' + pad(d2.getMonth() + 1) + '-' + pad(d2.getDate());
        out.time = pad(d2.getHours()) + ':' + pad(d2.getMinutes()) + ':' + pad(d2.getSeconds());
      }
    }
    return out;
  }

  // =========================================================================
  // 主绘制入口 / Main render entry point
  // =========================================================================

  /**
   * 把 shot JSON 画到 canvas 上。
   *
   * Draw a shot JSON onto a canvas.
   *
   * @param {Object} shotJson  DE1 shot JSON(与 sample_shots/ 里的格式一致)
   *                           DE1 shot JSON, same shape as sample_shots/
   * @param {HTMLCanvasElement} canvas 目标 canvas / target canvas
   * @param {Object} [options]
   *        lang      {string} 语言代码,默认 'zh' / language code, default 'zh'
   *        strings   {Object} 覆盖用的文案表 / label overrides
   *        machineId {string} 机器 ID,默认 'UNKNOWN' / machine ID
   *        scale     {number} 像素密度倍数,默认 1(见下)/ device pixel ratio
   *        background{string} 背景色,默认 '#fff' / background colour
   * @returns {{ok:boolean, error?:string}}
   *
   * 关于 scale:canvas 的 CSS 尺寸始终按 1296x576 的逻辑坐标走,内部像素尺寸
   * 是 1296*scale x 576*scale,用于在高 DPI 屏幕和打印时保持锐利。绘制坐标
   * 全部是逻辑坐标,缩放由 transform 统一处理。
   *
   * About `scale`: the canvas CSS size always follows the 1296x576 logical grid;
   * its backing store is 1296*scale x 576*scale so it stays sharp on high-DPI
   * screens and when printing. All drawing uses logical coordinates and the
   * scaling is applied once via a transform.
   */
  function renderShotToCanvas(shotJson, canvas, options) {
    options = options || {};
    var lang = options.lang || 'zh';
    var machineId = options.machineId || 'UNKNOWN';
    var scale = options.scale || 1;
    var t = resolveStrings(lang, options.strings);

    if (!canvas || !canvas.getContext) {
      return { ok: false, error: 'invalid canvas' };
    }
    var ctx = canvas.getContext('2d');

    var series = prepareSeries(shotJson);
    if (!series) {
      return { ok: false, error: 'too few samples to render' };
    }

    // 西文语言(单词长)右栏字号再小 2px(左图图表不受影响)
    // Latin-script languages get right-column fonts 2px smaller (words are
    // longer); the chart on the left is unaffected.
    var latinAdj = /[一-鿿]/.test(t.chart_pressure || '') ? 0 : -2;

    // 画布物理尺寸与逻辑坐标变换 / backing store size and logical transform
    canvas.width = Math.round(CHART_W * scale);
    canvas.height = Math.round(CHART_H * scale);
    ctx.setTransform(scale, 0, 0, scale, 0, 0);
    ctx.imageSmoothingEnabled = false;

    // 背景:先铺白再画,与 Image.new("L", ..., 255) 等价
    // Background: fill white first, equivalent to Image.new("L", ..., 255)
    ctx.fillStyle = options.background || '#fff';
    ctx.fillRect(0, 0, CHART_W, CHART_H);
    ctx.fillStyle = '#000';
    ctx.strokeStyle = '#000';

    var maxTime = series.elapsed.length ? series.elapsed[series.elapsed.length - 1] : 30;
    var xScale = (PLOT_R - PLOT_L) / Math.max(maxTime, 0.1);

    var fontTick = 16, fontLegend = 17, fontAxis = 17;

    // ---- 坐标轴 / Axes ----
    var axes = [AX_TEMP_X, AX_PRES_X, AX_FLOW_X];
    for (var ai = 0; ai < axes.length; ai++) {
      ctx.beginPath();
      ctx.lineWidth = 2;
      ctx.moveTo(axes[ai], PLOT_T);
      ctx.lineTo(axes[ai], PLOT_B);
      ctx.stroke();
    }
    // x 轴(时间)从左端 y 轴起画,三个 y 轴原点都与 x 轴重合
    // The time axis starts at the leftmost y-axis; all three y-axis origins
    // coincide with the x-axis.
    ctx.beginPath();
    ctx.lineWidth = 2;
    ctx.moveTo(AX_PRES_X, PLOT_B);
    ctx.lineTo(PLOT_R, PLOT_B);
    ctx.stroke();

    // ---- 网格与刻度:温度 0-100(最左),压力 0-10(左),流速 0-10(右) ----
    // ---- Grid & ticks: temperature 0-100 (leftmost), pressure 0-10 (left), flow 0-10 (right) ----
    // 每轴刻度贴各自轴摆放,刻度带互不重叠 / tick bands never overlap
    var axisSpecs = [
      { axis: AX_TEMP_X, yMax: 100, labelX: 57, align: 'rm' },
      { axis: AX_PRES_X, yMax: 10, labelX: 108, align: 'rm' },
      { axis: AX_FLOW_X, yMax: 10, labelX: 858, align: 'lm' }
    ];
    for (var s = 0; s < axisSpecs.length; s++) {
      var spec = axisSpecs[s];
      var ticks = niceTicks(0, spec.yMax, 12);
      for (var ti = 0; ti < ticks.length; ti++) {
        var v = ticks[ti];
        var iy = PLOT_B - (v / spec.yMax) * (PLOT_B - PLOT_T);
        if (spec.axis !== AX_TEMP_X) {
          dashLine(ctx, PLOT_L, iy, PLOT_R, iy, 1, [12, 8]);
        }
        ctx.beginPath();
        ctx.lineWidth = 1;
        ctx.moveTo(spec.axis - 4, iy);
        ctx.lineTo(spec.axis, iy);
        ctx.stroke();
        textAt(ctx, String(Math.round(v)), spec.labelX, iy, fontTick, spec.align);
      }
    }

    // ---- 时间轴刻度(下方)/ Time-axis ticks (below) ----
    var xTicks = niceTicks(0, maxTime, 10);
    for (var xi = 0; xi < xTicks.length; xi++) {
      var tv = xTicks[xi];
      var ix = PLOT_L + tv * xScale;
      ctx.beginPath();
      ctx.lineWidth = 1;
      ctx.moveTo(ix, PLOT_B);
      ctx.lineTo(ix, PLOT_B + 4);
      ctx.stroke();
      dashLine(ctx, ix, PLOT_T, ix, PLOT_B, 1, [10, 10]);
      textAt(ctx, String(Math.round(tv)), ix, PLOT_B + 6, fontTick, 'mt');
    }

    // ---- 四条曲线(实线 / 虚线 / 点线 / 点划线)----
    // ---- Four curves (solid / dashed / dotted / dash-dot) ----
    plotCurveStyle(ctx, series.elapsed, series.pressure, xScale, 10, 'solid');
    plotCurveStyle(ctx, series.elapsed, series.flow, xScale, 10, 'dashed');
    if (series.byWeight.length) {
      plotCurveStyle(ctx, series.elapsed, series.byWeight, xScale, 10, 'dotted');
    }
    plotCurveStyle(ctx, series.elapsed, series.basketTemp, xScale, 100, 'dashdot');

    // ---- 图例(4 列,下方)/ Legend (4 columns, below) ----
    var legendItems = [
      ['solid', t.chart_pressure],
      ['dashed', t.chart_water_flow],
      ['dotted', t.chart_coffee_flow],
      ['dashdot', t.chart_temperature]
    ];
    var lx = PLOT_L;
    for (var li = 0; li < legendItems.length; li++) {
      var style = legendItems[li][0], label = legendItems[li][1];
      if (style === 'solid') {
        ctx.beginPath();
        ctx.lineWidth = 3;
        ctx.moveTo(lx, LEGEND_Y);
        ctx.lineTo(lx + 30, LEGEND_Y);
        ctx.stroke();
      } else if (style === 'dashed') {
        dashLine(ctx, lx, LEGEND_Y, lx + 30, LEGEND_Y, 3, [10, 6]);
      } else if (style === 'dotted') {
        var dots = [6, 15, 24];
        for (var di = 0; di < dots.length; di++) {
          ctx.beginPath();
          ctx.arc(lx + dots[di], LEGEND_Y, 2, 0, Math.PI * 2);
          ctx.fill();
        }
      } else {
        dashLine(ctx, lx, LEGEND_Y, lx + 30, LEGEND_Y, 3, [8, 4, 2, 4]);
      }
      textAt(ctx, label, lx + 36, LEGEND_Y - 9, fontLegend, 'la');
      lx += 36 + textWidth(ctx, label, fontLegend) + 34;
    }

    // ---- 机器 ID(左下角,方框按文字实际尺寸 + 内边距,紧贴图例)----
    // ---- Machine ID (bottom-left; box sized to text + padding, snug below the legend) ----
    var mid = (t.machine_id || '') + machineId;
    if (mid.replace(/\s/g, '')) {
      var mw = textWidth(ctx, mid, fontTick);
      var fmTick = fontMetrics(ctx, fontTick);
      var th = fmTick.ascent + fmTick.descent;
      var pad = 4;
      var bx = 16, by = CHART_H - 68;
      roundRect(ctx, bx, by, mw + pad * 2, th + pad * 2, 4);
      ctx.beginPath();
      ctx.lineWidth = 1;
      ctx.stroke();
      textAt(ctx, mid, bx + pad, by + pad, fontTick, 'la');
    }

    // ---- 第一列文本(冲煮信息)/ Column 1 text (brew info) ----
    var data = shotJson || {};
    var profileTitle = (data.profile && data.profile.title) || t.chart_unknown_profile;
    var profileFontSize = fitFontSize(ctx, profileTitle, 20 + latinAdj, COL1_MAXW);
    var profileLines = wrapByWidth(ctx, profileTitle, profileFontSize, COL1_MAXW - 10, 14);

    var meta = data.meta || {};
    var inWeight = meta.in || 'N/A';
    var outWeight = meta.out || 'N/A';
    var shotTime = meta.time || 'N/A';
    var grinderSetting = (meta.grinder && meta.grinder.setting) || 'N/A';

    var dt = parseDateTime(data);
    var initialTemp = series.basketTemp.length ? series.basketTemp[0] : 0;

    var fontCol1Title = 22 + latinAdj;
    var fontCol1 = 20 + latinAdj;

    var sep = '──────';
    var col1 = [];
    col1.push([t.chart_date_time, true, null]);
    col1.push([sep, false, null]);
    col1.push([dt.date, false, null]);
    col1.push([dt.time, false, null]);
    col1.push(['', false, null]);
    col1.push([t.chart_profile, true, null]);
    col1.push([sep, false, null]);
    var pLines = profileLines.length ? profileLines : [String(profileTitle).substring(0, 7)];
    for (var pi = 0; pi < pLines.length; pi++) col1.push([pLines[pi], false, profileFontSize]);
    col1.push(['', false, null]);
    col1.push([t.chart_extraction, true, null]);
    col1.push([sep, false, null]);
    col1.push([t.chart_in_weight + ': ' + inWeight + 'g', false, null]);
    col1.push([t.chart_out_weight + ': ' + outWeight + 'g', false, null]);
    col1.push([t.chart_shot_time + ': ' + shotTime + 's', false, null]);
    col1.push(['', false, null]);
    col1.push([t.chart_grinder_temp, true, null]);
    col1.push([sep, false, null]);
    col1.push([t.chart_grind_setting + ': ' + grinderSetting, false, null]);
    col1.push([t.chart_initial_temp + ': ' + initialTemp.toFixed(1) + '°C', false, null]);

    drawTextColumn(ctx, col1, COL1_X, fontCol1Title, fontCol1);

    // ---- 第二列文本(豆子信息或方案信息)/ Column 2 text (bean info or profile info) ----
    var beanData = (meta.bean) || {};
    var hasBeanInfo = !!(beanData && (beanData.brand || beanData.type || beanData.notes));

    // 按文本长度调字号,再按渲染宽度换行 / fit the font, then wrap by width
    var beanBlock = function (text) {
      if (!text) return [];
      var f = fitFontSize(ctx, text, 17 + latinAdj, COL2_MAXW);
      var ws = wrapByWidth(ctx, text, f, COL2_MAXW - 10, 16);
      var out = [];
      for (var i = 0; i < ws.length; i++) out.push([ws[i], false, f]);
      return out;
    };

    var col2 = [];
    col2.push([hasBeanInfo ? t.chart_bean_info : t.chart_profile_info, true, null]);
    col2.push([sep, false, null]);

    if (hasBeanInfo) {
      var brand = beanData.brand || '';
      var beanType = beanData.type || '';
      var line1 = (brand && beanType) ? (brand + ' - ' + beanType) : (brand || beanType);
      var line2 = beanData.notes || '';
      var roastInfo = [];
      if (beanData.roast_level) roastInfo.push(beanData.roast_level);
      var roastDate = beanData.roast_date || '';
      if (String(roastDate).length === 8 && /^\d+$/.test(String(roastDate))) {
        roastInfo.push(roastDate.substring(0, 4) + '-' + roastDate.substring(4, 6) + '-' + roastDate.substring(6, 8));
      }
      var line3 = roastInfo.join(' ');
      var blocks = [line1, line2, line3];
      for (var bi = 0; bi < blocks.length; bi++) {
        col2 = col2.concat(beanBlock(blocks[bi]));
      }
      var shotNotes = (meta.shot && meta.shot.notes) || '';
      if (shotNotes) {
        col2.push(['Tasting Note (from JSON):', true, null]);
        col2.push([sep, false, null]);
        col2 = col2.concat(beanBlock(shotNotes));
      }
    } else {
      var notes = (data.profile && data.profile.notes) || '';
      if (notes) col2 = col2.concat(beanBlock(notes));
      else col2.push([t.chart_na, false, null]);
    }

    // 固定品尝笔记区域 / fixed tasting-note area
    col2.push(['', false, null]);
    col2.push([t.chart_tasting_note, true, null]);
    col2.push([sep, false, null]);
    for (var blank = 0; blank < 4; blank++) col2.push(['', false, null]);

    drawTextColumn(ctx, col2, COL2_X, 19 + latinAdj, 17 + latinAdj);

    // ---- 轴标题(竖向,17px = 与图例同大;最后绘制,盖住轴与刻度)----
    // ---- Axis titles (vertical, 17px = legend size; drawn last so they sit on top) ----
    var axisTitles = [
      [16, t.chart_temperature],
      [74, t.chart_pressure],
      [866, t.chart_flow]
    ];
    var midY = (PLOT_T + PLOT_B) / 2;
    for (var at = 0; at < axisTitles.length; at++) {
      vText(ctx, axisTitles[at][0], midY, axisTitles[at][1], fontAxis);
    }
    // 再补画一次:防止右侧文本块等任何后续绘制覆盖流速标题
    // Draw once more so nothing drawn later can cover the flow title.
    for (var at2 = 0; at2 < axisTitles.length; at2++) {
      vText(ctx, axisTitles[at2][0], midY, axisTitles[at2][1], fontAxis);
    }

    return { ok: true, width: CHART_W, height: CHART_H, scale: scale };
  }

  /** 画一列有行距的文本 / Draw one column of line-spaced text. */
  function drawTextColumn(ctx, items, x, titleSize, bodySize) {
    var y = 52;
    for (var i = 0; i < items.length; i++) {
      var text = items[i][0];
      var isTitle = items[i][1];
      var overrideSize = items[i][2];
      if (text === '──────') {
        y -= LINE_H * 0.5;
      } else if (text === '') {
        y -= LINE_H * 0.3;
      } else {
        var px = overrideSize || (isTitle ? titleSize : bodySize);
        textAt(ctx, text, x, y, px, 'la');
      }
      y += LINE_H;
    }
  }

  /** 圆角矩形路径 / Rounded-rectangle path. */
  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.lineTo(x + w - r, y);
    ctx.quadraticCurveTo(x + w, y, x + w, y + r);
    ctx.lineTo(x + w, y + h - r);
    ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
    ctx.lineTo(x + r, y + h);
    ctx.quadraticCurveTo(x, y + h, x, y + h - r);
    ctx.lineTo(x, y + r);
    ctx.quadraticCurveTo(x, y, x + r, y);
    ctx.closePath();
  }

  // =========================================================================
  // 位图导出 / Bitmap export
  // =========================================================================

  /**
   * 把 canvas 转成 1-bit 单色位图(打印用)。
   *
   * Convert a canvas into 1-bit monochrome bitmap data for printing.
   *
   * 输出格式 / Output format:
   *   - 每行按 MSB-first 打包成字节,行宽向上补齐到 8 的整数倍
   *     (正是 ESC/POS 的 GS v 0 光栅位图所需的形式,也是 PBM P4 的位序)
   *   - 每行从左到右 = 字节的高位到低位 / each row is packed MSB-first, left to right
   *   - 位为 1 = 黑点,0 = 白 / a set bit means a black dot
   *
   * 旋转与缩放 / Rotation and scaling:
   *   小票宽度有限,横向的曲线图必须旋转 90° 才能铺满纸宽。打印时默认旋转,
   *   并把长边缩放到目标点数宽度(80mm 机一般 576 点)。
   *
   *   A receipt is narrow, so the landscape chart must be rotated 90° to span the
   *   paper width. Printing therefore rotates by default and scales the long edge
   *   to the target dot width (576 dots is typical for an 80mm printer).
   *
   * @param {HTMLCanvasElement} canvas
   * @param {Object} [options]
   *        targetWidth {number} 旋转后的目标点数宽度,默认 576 / dot width after rotation
   *        rotate      {boolean} 是否旋转 90°,默认 true / rotate 90°, default true
   *        threshold   {number} 二值化阈值,默认 200(>阈值判为白)/ binarisation threshold
   * @returns {{width:number, height:number, data:Uint8Array, bytesPerRow:number}}
   */
  function canvasToBitmap(canvas, options) {
    options = options || {};
    var rotate = options.rotate !== false;
    var threshold = typeof options.threshold === 'number' ? options.threshold : DEFAULT_THRESHOLD;

    var srcW = canvas.width;
    var srcH = canvas.height;

    // 目标尺寸:旋转后长边方向 = 纸宽方向 / after rotation the long edge runs across the paper
    var outW, outH;
    if (rotate) {
      var targetW = options.targetWidth || DEFAULT_PRINT_WIDTH;
      var ratio = targetW / srcH;             // 旋转后宽度来自源图高度 / post-rotation width comes from source height
      outW = targetW;
      outH = Math.round(srcW * ratio);
    } else {
      outW = options.targetWidth || srcW;
      outH = Math.round(srcH * (outW / srcW));
    }

    // 用一张离屏 canvas 做旋转 + 缩放,得到灰度像素
    // Rotate and scale on an offscreen canvas to get grayscale pixels
    var off = document.createElement('canvas');
    off.width = outW;
    off.height = outH;
    var octx = off.getContext('2d');
    octx.fillStyle = '#fff';
    octx.fillRect(0, 0, outW, outH);
    octx.imageSmoothingEnabled = true;
    octx.imageSmoothingQuality = 'high';

    octx.save();
    if (rotate) {
      // 旋转 90°(与旧版 Pillow 的 img.rotate(90, expand=True) 方向一致)
      // Rotate 90° in the same direction as the old Pillow img.rotate(90, expand=True)
      octx.translate(outW, 0);
      octx.rotate(Math.PI / 2);
      octx.drawImage(canvas, 0, 0, outH, outW);
    } else {
      octx.drawImage(canvas, 0, 0, outW, outH);
    }
    octx.restore();

    var img = octx.getImageData(0, 0, outW, outH);
    var px = img.data;
    var bytesPerRow = Math.ceil(outW / 8);
    var data = new Uint8Array(bytesPerRow * outH);

    for (var y = 0; y < outH; y++) {
      var rowBase = y * outW * 4;
      var byteBase = y * bytesPerRow;
      for (var x = 0; x < outW; x++) {
        var o = rowBase + x * 4;
        // 亮度加权(Rec.601);alpha 忽略,离屏 canvas 已铺白底
        // Luma-weighted (Rec.601); alpha is ignored, the offscreen canvas is pre-filled white
        var lum = 0.299 * px[o] + 0.587 * px[o + 1] + 0.114 * px[o + 2];
        if (lum <= threshold) {
          data[byteBase + (x >> 3)] |= (0x80 >> (x & 7));
        }
      }
    }

    return { width: outW, height: outH, data: data, bytesPerRow: bytesPerRow };
  }

  /**
   * 把 1-bit 位图打包成 base64,供 /api/print 传输。
   * Pack a 1-bit bitmap into base64 for the /api/print endpoint.
   */
  function bitmapToBase64(bitmap) {
    var bytes = bitmap.data;
    var chunk = 0x8000;
    var parts = [];
    for (var i = 0; i < bytes.length; i += chunk) {
      parts.push(String.fromCharCode.apply(null, bytes.subarray(i, i + chunk)));
    }
    return btoa(parts.join(''));
  }

  // =========================================================================
  // 导出 / Exports
  // =========================================================================
  var api = {
    CHART_W: CHART_W,
    CHART_H: CHART_H,
    DEFAULT_PRINT_WIDTH: DEFAULT_PRINT_WIDTH,
    DEFAULT_THRESHOLD: DEFAULT_THRESHOLD,
    FONT_STACK: FONT_STACK,
    STRINGS: STRINGS,
    ensureFontReady: ensureFontReady,
    renderShotToCanvas: renderShotToCanvas,
    canvasToBitmap: canvasToBitmap,
    bitmapToBase64: bitmapToBase64,
    // 以下为内部件,导出以便测试与调试 / internals exported for tests and debugging
    _prepareSeries: prepareSeries,
    _niceTicks: niceTicks,
    _canvasToBitmap: canvasToBitmap
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  global.PrintTheShotRender = api;
})(typeof window !== 'undefined' ? window : globalThis);
