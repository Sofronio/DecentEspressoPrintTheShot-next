/**
 * PrintTheShot — 蓝牙打印机设置 / Bluetooth printer settings
 * =========================================================
 *
 * 中文
 * ----
 * 只在 Android 上有意义的界面:申请蓝牙权限、列出已配对的打印机、连接、看连接
 * 状态、打一张测试页。
 *
 * 为什么这页不能省:插件本身只是「能力」,没人调用它就没有任何作用。用户装上
 * App 之后要做的第一件事就是配对打印机 —— 没有这个页面,前两步(授权、选设备)
 * 无从下手,整个 App 就是个摆设。
 *
 * 设计上的两个决定:
 *
 *   1. **连接状态是轮询的,不是靠事件。** 蓝牙断开的方式太多(打印机没电、
 *      走远了、被别的设备抢走连接),靠事件监听一定会漏。定时问一次「还连着吗」
 *      虽然笨,但不会漏。
 *
 *   2. **选中的打印机地址存在本地。** 下次打开 App 自动重连 —— 用户配一次就够了。
 *      没存地址时不去猜「用第一台设备」,因为猜错的表现是「点了打印没反应」,
 *      比直接说「还没选打印机」难查得多。
 *
 * English
 * -------
 * A screen that only means anything on Android: request Bluetooth permission, list
 * paired printers, connect, watch connection status, print a test page.
 *
 * Why it cannot be skipped: the plugin is only a capability, and a capability nobody
 * calls does nothing. The first thing a user does after installing is pair a printer —
 * without this screen the first two steps (permission, choosing a device) have nowhere
 * to happen and the app is decoration.
 *
 * Two design decisions:
 *
 *   1. **Connection status is polled, not event-driven.** Bluetooth drops in too many
 *      ways (printer out of battery, walked out of range, another device grabbing the
 *      connection) and an event listener will always miss one. Asking "still
 *      connected?" on a timer is duller but it does not miss.
 *
 *   2. **The chosen printer's address is stored locally** so the next launch
 *      reconnects on its own — configure once. When nothing is stored we do not guess
 *      "just use the first device": guessing wrong shows up as "I pressed print and
 *      nothing happened", which is far harder to diagnose than a plain "no printer
 *      selected yet".
 */

(function (global) {
  'use strict';

  var PRINTER_KEY = 'pts_bt_printer';
  var POLL_MS = 4000;

  var pollTimer = null;
  var connectedAddress = '';
  var devices = [];

  function P() { return global.PrintTheShotPrinter; }

  function plugin() {
    if (!global.Capacitor || !global.Capacitor.isNativePlatform ||
        !global.Capacitor.isNativePlatform()) return null;
    return (global.Capacitor.Plugins && global.Capacitor.Plugins.PrintTheShotPrinter) || null;
  }

  function T(key, fallback) {
    var L = global.L || {};
    return L[key] || fallback || key;
  }

  function toast(msg) {
    if (typeof global.toast === 'function') global.toast(msg);
  }

  // ------------------------------------------------------------------ 记忆
  // ------------------------------------------------------------------ persistence

  function getSavedPrinter() {
    try { return global.localStorage.getItem(PRINTER_KEY) || ''; }
    catch (e) { return ''; }
  }

  function setSavedPrinter(address) {
    try {
      if (address) global.localStorage.setItem(PRINTER_KEY, address);
      else global.localStorage.removeItem(PRINTER_KEY);
    } catch (e) { /* 隐私模式 / private mode */ }
  }

  // ------------------------------------------------------------------ 状态
  // ------------------------------------------------------------------ state

  async function refreshStatus() {
    var el = document.getElementById('bt-status');
    if (!el) return;
    var p = plugin();
    if (!p) { el.textContent = '—'; return; }
    try {
      var r = await p.isConnected();
      connectedAddress = (r && r.connected) ? (r.address || '') : '';
      var saved = getSavedPrinter();
      if (connectedAddress) {
        el.innerHTML = '<span style="color:#0a0">●</span> ' +
          T('bt_connected', 'Connected') + ' · ' + escapeHtml(connectedAddress);
      } else if (saved) {
        el.innerHTML = '<span style="color:#c80">●</span> ' +
          T('bt_saved_not_connected', 'Selected but not connected') + ' · ' + escapeHtml(saved);
      } else {
        el.innerHTML = '<span style="color:#999">●</span> ' +
          T('bt_none', 'No printer selected');
      }
    } catch (e) {
      el.textContent = '⚠️ ' + (e.message || e);
    }
  }

  function startPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(refreshStatus, POLL_MS);
    refreshStatus();
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  // ------------------------------------------------------------------ 设备
  // ------------------------------------------------------------------ devices

  async function requestPermissions() {
    var p = plugin();
    if (!p) return false;
    var note = document.getElementById('bt-note');
    try {
      if (note) note.textContent = '⏳ ' + T('bt_requesting', 'Requesting permission…');
      var r = await p.requestPermissions();
      var granted = !!(r && r.granted);
      if (note) {
        note.textContent = granted
          ? '✅ ' + T('bt_granted', 'Permission granted')
          : '❌ ' + T('bt_denied', 'Permission denied — grant Bluetooth access in Android settings');
      }
      return granted;
    } catch (e) {
      if (note) note.textContent = '❌ ' + (e.message || e);
      return false;
    }
  }

  async function loadDevices(askPermission) {
    var list = document.getElementById('bt-list');
    var note = document.getElementById('bt-note');
    if (!list) return;
    list.innerHTML = '<div class="empty">⏳ ' + T('bt_scanning', 'Loading paired devices…') + '</div>';

    if (askPermission) await requestPermissions();

    try {
      var r = await P().listPrinters();
      devices = (r && r.printers) || [];
    } catch (e) {
      list.innerHTML = '<div class="empty">❌ ' + escapeHtml(e.message || e) + '</div>';
      return;
    }

    if (!devices.length) {
      // 「列表是空的」有两个完全不同的原因,必须分开说 —— 一个是没配对,一个是
      // 没给权限。混成一句「没有设备」会让用户去系统设置里白找一圈。
      //
      // An empty list has two very different causes and they must be told apart: not
      // paired, versus permission never granted. Lumping them into "no devices" sends
      // the user on a pointless trip through system settings.
      list.innerHTML = '<div class="empty">' + T('bt_empty',
        'No paired printers yet. Pair the printer in Android Settings → Bluetooth first.') + '</div>';
      if (note) note.textContent = 'ℹ️ ' + T('bt_empty_hint',
        'If the printer is already paired, Bluetooth permission is probably missing.');
      return;
    }

    var saved = getSavedPrinter();
    list.innerHTML = devices.map(function (d) {
      var isSaved = d.address === saved;
      var isConn = d.address === connectedAddress;
      var badge = isConn ? '<span style="color:#0a0">●</span> '
        : (isSaved ? '<span style="color:#c80">●</span> ' : '');
      return '<div class="bt-device" style="display:flex;justify-content:space-between;'
        + 'align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--border)">'
        + '<div>' + badge + '<b>' + escapeHtml(d.name || d.address) + '</b>'
        + '<div style="font-size:11px;color:var(--muted)">' + escapeHtml(d.address)
        + (d.paired ? '' : ' · ' + T('bt_unpaired', 'not paired')) + '</div></div>'
        + '<div style="display:flex;gap:6px">'
        + '<button class="btn" data-addr="' + escapeHtml(d.address) + '" data-act="connect">'
        + (isConn ? T('bt_reconnect', 'Reconnect') : T('bt_connect', 'Connect')) + '</button>'
        + '</div></div>';
    }).join('');

    list.querySelectorAll('button[data-act="connect"]').forEach(function (btn) {
      btn.addEventListener('click', function () { connect(btn.dataset.addr); });
    });

    if (note) note.textContent = '';
  }

  async function connect(address) {
    var p = plugin();
    if (!p) return;
    var note = document.getElementById('bt-note');
    if (note) note.textContent = '⏳ ' + T('bt_connecting', 'Connecting…');
    try {
      var r = await p.connect({ address: address });
      if (r && r.success) {
        setSavedPrinter(address);
        if (note) note.textContent = '✅ ' + T('bt_connect_ok', 'Connected');
        toast('✅ ' + T('bt_connect_ok', 'Connected'));
      } else {
        if (note) note.textContent = '❌ ' + ((r && r.message) || T('bt_connect_fail', 'Connection failed'));
      }
    } catch (e) {
      if (note) note.textContent = '❌ ' + (e.message || e);
    }
    await refreshStatus();
    await loadDevices(false);
  }

  async function disconnect() {
    var p = plugin();
    if (!p) return;
    try { await p.disconnect(); } catch (e) { /* 已经断了就算了 / already gone is fine */ }
    await refreshStatus();
    await loadDevices(false);
  }

  /**
   * 打一张测试页。用一张固定的测试图,不依赖任何 shot 数据 —— 它的用途是在
   * 「还没接到 DE1」的阶段就能确认打印机通不通。
   *
   * Print a test page from a fixed pattern rather than real shot data: its job is to
   * prove the printer works before any DE1 is involved.
   */
  async function testPrint() {
    var p = plugin();
    if (!p) { toast('❌ ' + T('bt_android_only', 'Bluetooth printing is Android-only')); return; }
    var address = connectedAddress || getSavedPrinter();
    if (!address) { toast('❌ ' + T('bt_none', 'No printer selected')); return; }

    var note = document.getElementById('bt-note');
    if (note) note.textContent = '⏳ ' + T('bt_testing', 'Sending a test page…');

    try {
      // 直接调 printer.js 里那份画图与打包逻辑,不另写一套 —— 测试页和真图走
      // 同一条代码路径,才有「测过 = 能用」的意义。
      //
      // Reuse the drawing and packing code in printer.js rather than writing a
      // second one: a test page only means something if it travels the same code
      // path as a real chart.
      var R = global.PrintTheShotRender;
      var canvas = document.createElement('canvas');
      var res = R.renderShotToCanvas(TEST_SHOT, canvas, { lang: 'zh', machineId: 'TEST', scale: 1 });
      if (!res.ok) throw new Error(res.error);

      var bitmap = R.canvasToBitmap(canvas, { targetWidth: 576, rotate: true, threshold: 200 });
      var job = P().buildJob(bitmap, { feedLines: 3, cut: true });
      var r = await p.printRaw({ data: P().bytesToBase64(job), address: address });

      if (r && r.success) {
        if (note) note.textContent = '✅ ' + T('bt_test_ok', 'Test page sent — check the printer');
      } else {
        if (note) note.textContent = '❌ ' + ((r && r.message) || T('bt_test_fail', 'Test print failed'));
      }
    } catch (e) {
      if (note) note.textContent = '❌ ' + (e.message || e);
    }
  }

  /** 一张最小的合成数据,只为把测试页画满 / minimal synthetic data to fill a test page. */
  var TEST_SHOT = (function () {
    var n = 60, elapsed = [], p = [], f = [], bw = [], t = [];
    for (var i = 0; i < n; i++) {
      var x = i / (n - 1);
      elapsed.push(String((x * 25).toFixed(2)));
      p.push(String((9 * Math.min(1, x * 4)).toFixed(2)));
      f.push(String((2.5 * Math.sin(x * 3.14)).toFixed(2)));
      bw.push(String((25 * x).toFixed(2)));
      t.push(String((88 + 5 * Math.sin(x * 2)).toFixed(2)));
    }
    return {
      elapsed: elapsed,
      pressure: { pressure: p },
      flow: { flow: f, by_weight: bw },
      temperature: { basket: t },
      profile: { title: 'TEST PAGE 测试页', notes: '' },
      meta: {
        in: '18.0', out: '36.0', time: '25.0', grinder: { setting: '1.0' },
        bean: { brand: 'PrintTheShot', type: 'Bluetooth test', notes: '', roast_level: '', roast_date: '' },
        shot: { notes: '' }
      },
      timestamp: String(Math.floor(Date.now() / 1000))
    };
  })();

  // ------------------------------------------------------------------ 界面
  // ------------------------------------------------------------------ the UI

  function mount() {
    var card = document.getElementById('bt-card');
    if (!card) return;
    if (!plugin()) {
      // 桌面浏览器里没有蓝牙打印这回事,整张卡片直接不显示
      // There is no Bluetooth printing in a desktop browser, so the card stays away
      card.style.display = 'none';
      return;
    }
    card.style.display = '';
    card.innerHTML = ''
      + '<h2>' + T('bt_title', 'Bluetooth printer') + '</h2>'
      + '<div class="stat"><div class="label">' + T('bt_status_label', 'Status')
      + '</div><div class="value" id="bt-status">—</div></div>'
      + '<div style="margin:12px 0;display:flex;gap:8px;flex-wrap:wrap">'
      + '<button class="btn" id="bt-perm">' + T('bt_request', 'Grant permission') + '</button>'
      + '<button class="btn gray" id="bt-refresh">' + T('bt_refresh', 'Refresh list') + '</button>'
      + '<button class="btn gray" id="bt-disc">' + T('bt_disconnect', 'Disconnect') + '</button>'
      + '<button class="btn" id="bt-test">' + T('bt_test', 'Test print') + '</button>'
      + '</div>'
      + '<div id="bt-list"></div>'
      + '<div class="note" id="bt-note"></div>';

    document.getElementById('bt-perm').addEventListener('click', function () { loadDevices(true); });
    document.getElementById('bt-refresh').addEventListener('click', function () { loadDevices(false); });
    document.getElementById('bt-disc').addEventListener('click', disconnect);
    document.getElementById('bt-test').addEventListener('click', testPrint);

    startPolling();
    loadDevices(true);   // 首次进来先要权限,否则列表多半是空的
  }

  global.PrintTheShotBluetooth = {
    mount: mount,
    refreshStatus: refreshStatus,
    loadDevices: loadDevices,
    connect: connect,
    testPrint: testPrint,
    getSavedPrinter: getSavedPrinter,
    setSavedPrinter: setSavedPrinter
  };
})(typeof window !== 'undefined' ? window : globalThis);
