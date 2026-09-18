#!/usr/bin/env node
/**
 * PrintTheShot — 出图工具 / mockup exporter
 * ==========================================
 *
 * 中文
 * ----
 * 用真实的无头 Chrome 跑 tests/mockup.html,把两张 PNG 写到桌面:
 *
 *   pts_chart_<lang>.png     界面里看到的图表(横向 1296×576)
 *   pts_print_<lang>.png     送去打印机的 1-bit 位图(旋转 90°)
 *
 * 走真实浏览器是刻意的 —— 这两张图要能代表线上跑出来的样子,就得用线上那份
 * render.js,而不是另写一段近似的绘制代码。
 *
 * 用法 / usage:
 *   node tests/make_mockup.mjs [shotFile] [lang] [width]
 *   node tests/make_mockup.mjs shot_20260831_111104_1788145864180024.json zh 576
 *
 * English
 * -------
 * Runs tests/mockup.html in a real headless Chrome and writes two PNGs to the
 * Desktop:
 *
 *   pts_chart_<lang>.png     the chart the UI shows (landscape 1296x576)
 *   pts_print_<lang>.png     the 1-bit bitmap the printer receives (rotated 90°)
 *
 * Using a real browser is deliberate: for these images to represent what actually
 * ships, they have to come from the shipping render.js rather than an approximation
 * written separately.
 */

import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import { writeFileSync, existsSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

const SHOT = process.argv[2] || 'prodigal_el_rafugio.json';
const LANG = process.argv[3] || 'zh';
const WIDTH = process.argv[4] || '576';
const PAD = process.argv[5] || '0.5';   // 位图四周留白(mm 级)/ margin around the bitmap
const PORT = 9334;
const BASE = process.env.PTS_BASE || 'http://localhost:8790';

const CHROME_CANDIDATES = [
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/Applications/Chromium.app/Contents/MacOS/Chromium',
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
];

function findChrome() {
  for (const p of CHROME_CANDIDATES) if (existsSync(p)) return p;
  throw new Error('Chrome not found');
}

class CDP {
  constructor(ws) {
    this.ws = ws; this.id = 0; this.pending = new Map();
    ws.addEventListener('message', (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id && this.pending.has(m.id)) {
        const { resolve, reject } = this.pending.get(m.id);
        this.pending.delete(m.id);
        if (m.error) reject(new Error(JSON.stringify(m.error)));
        else resolve(m.result);
      }
    });
  }
  send(method, params = {}) {
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }
  async evaluate(expression) {
    const r = await this.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.text);
    return r.result.value;
  }
}

/** 把 data URL 存成 PNG / save a data URL as a PNG. */
function saveDataUrl(dataUrl, path) {
  const b64 = dataUrl.replace(/^data:image\/png;base64,/, '');
  writeFileSync(path, Buffer.from(b64, 'base64'));
  return Buffer.byteLength(b64, 'base64');
}

async function main() {
  const chrome = findChrome();
  const proc = spawn(chrome, [
    '--headless=new', '--disable-gpu', '--no-sandbox', '--hide-scrollbars',
    '--no-first-run', `--remote-debugging-port=${PORT}`,
    '--window-size=1400,1200', 'about:blank',
  ], { stdio: ['ignore', 'ignore', 'ignore'] });
  const cleanup = () => { try { proc.kill('SIGKILL'); } catch { } };
  process.on('exit', cleanup);

  let up = false;
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/json/version`);
      if (r.ok) { up = true; break; }
    } catch { }
    await sleep(250);
  }
  if (!up) { cleanup(); throw new Error('Chrome did not expose a debugging port'); }

  const url = `${BASE}/tests/mockup.html?file=${encodeURIComponent(SHOT)}` +
              `&lang=${LANG}&width=${WIDTH}&pad=${PAD}`;
  const target = await (await fetch(
    `http://127.0.0.1:${PORT}/json/new?${encodeURIComponent(url)}`, { method: 'PUT' })).json();

  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => {
    ws.addEventListener('open', res, { once: true });
    ws.addEventListener('error', rej, { once: true });
  });
  const cdp = new CDP(ws);
  await cdp.send('Runtime.enable');

  let raw = null;
  for (let i = 0; i < 200; i++) {
    raw = await cdp.evaluate(
      `(function(){var e=document.getElementById('MOCKUP_JSON');return e?e.textContent:null;})()`
    ).catch(() => null);
    if (raw) break;
    await sleep(250);
  }

  if (!raw) {
    const why = await cdp.evaluate(`document.getElementById('status').textContent`).catch(() => '?');
    ws.close(); cleanup();
    throw new Error('no result from the mockup page: ' + why);
  }

  const data = JSON.parse(raw);
  ws.close(); cleanup();
  if (!data.ok) throw new Error('render failed: ' + data.error);

  const desktop = join(homedir(), 'Desktop');
  const chartPath = join(desktop, `pts_chart_${LANG}.png`);
  const printPath = join(desktop, `pts_print_${LANG}.png`);
  const chartBytes = saveDataUrl(data.chart, chartPath);
  const printBytes = saveDataUrl(data.bitmap, printPath);

  console.log('图表 / chart        :', chartPath);
  console.log('  尺寸 / size       :', data.chartSize.join(' × '), ' px,', (chartBytes / 1024).toFixed(1), 'KB');
  console.log('  墨迹比 / ink      :', (data.inkChart * 100).toFixed(2) + '%');
  console.log('打印位图 / bitmap   :', printPath);
  console.log('  尺寸 / size       :', data.bitmapSize.join(' × '), ' dots,', (printBytes / 1024).toFixed(1), 'KB');
  console.log('  数据量 / payload  :', data.bytes, 'bytes,', data.bytesPerRow, 'bytes/row');
  console.log('  置位 / set bits   :', data.setBits.toLocaleString(),
              '(' + (data.inkBitmapData * 100).toFixed(2) + '% of dots)');
  console.log('  ESC/POS 头        :', data.escpos);
  console.log('数据源 / source     :', data.file, '(' + data.lang + ')');

  // 内容为空就直接报错退出。尺寸正确但内容全白是最难发现的一种失败 ——
  // 它不抛异常,光看日志也看不出问题,只有出图之后才会发现纸上一片空白。
  //
  // A blank payload is a hard error here. "Right size, no content" is the hardest
  // kind of failure to notice: nothing throws, the log looks healthy, and the
  // problem only shows up as an empty sheet of paper.
  if (data.setBits === 0) {
    console.error('');
    console.error('❌ 位图内容为空(全白)/ the bitmap payload is empty (all white)');
    console.error('   尺寸对但一个点都没置位 —— 检查出图这一步有没有真的画上去');
    console.error('   correct size but zero set bits — check that the export step draws anything');
    process.exit(1);
  }
}

main().catch((e) => { console.error('❌ ' + (e.stack || e.message)); process.exit(1); });
