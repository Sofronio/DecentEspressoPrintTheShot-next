#!/usr/bin/env node
/**
 * PrintTheShot — 浏览器测试驱动 / Browser test driver
 * ===================================================
 *
 * 中文
 * ----
 * 用 Chrome DevTools Protocol 驱动一个真实的无头 Chrome,打开测试页,轮询等它
 * 把结果写进 DOM,然后读出来。退出码 = 失败数,方便直接在 CI 或 shell 里判断。
 *
 * 为什么不用 --dump-dom:
 *   --dump-dom 配 --virtual-time-budget 的时机不好控制 —— 它按虚拟时钟判断
 *   「可以 dump 了」,而 iframe 加载 + 字体加载 + 网络请求都是真实耗时。结果是
 *   测试还没跑完就 dump 了,拿不到结果。走 CDP 可以自己决定什么时候读。
 *
 * 用法 / usage:
 *   node tests/run_web_tests.mjs [baseUrl] [page]
 *   node tests/run_web_tests.mjs http://localhost:8780 /tests/web_test.html
 *   node tests/run_web_tests.mjs http://localhost:8780 /tests/ui_test.html
 *
 * English
 * -------
 * Drives a real headless Chrome over the DevTools Protocol: opens the test page,
 * polls until the page writes its results into the DOM, then reads them. The exit
 * code is the number of failures, so it drops straight into CI or a shell check.
 *
 * Why not --dump-dom: its timing under --virtual-time-budget is hard to control —
 * it decides "time to dump" on the virtual clock, while iframe loading, font
 * loading and network requests all take real time. The result is a dump taken
 * before the tests finished, with nothing to read. With CDP we choose when to look.
 *
 * Usage:
 *   node tests/run_web_tests.mjs [baseUrl] [page]
 */

import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import { existsSync } from 'node:fs';

const BASE = process.argv[2] || 'http://localhost:8780';
const PAGE = process.argv[3] || '/tests/web_test.html';
const PORT = 9333;

const CHROME_CANDIDATES = [
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/Applications/Chromium.app/Contents/MacOS/Chromium',
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
  '/usr/bin/chromium-browser',
];

function findChrome() {
  for (const p of CHROME_CANDIDATES) if (existsSync(p)) return p;
  throw new Error('Chrome not found. Tried:\n  ' + CHROME_CANDIDATES.join('\n  '));
}

/** 极简 CDP 客户端 / a minimal CDP client. */
class CDP {
  constructor(ws) {
    this.ws = ws;
    this.id = 0;
    this.pending = new Map();
    ws.addEventListener('message', (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        if (msg.error) reject(new Error(JSON.stringify(msg.error)));
        else resolve(msg.result);
      }
    });
  }
  /**
   * 发一条 CDP 命令。**必须有超时** —— 没有的话,一旦 WebSocket 那头再也不回消息
   * (实测遇到过:页面早就跑完了,结果也写进了 DOM,但 node 永远等不到回应),
   * 这里返回的 Promise 就永远悬着,整个测试套件挂到天荒地老,而且不报任何错。
   * 卡死比失败糟糕得多:失败会告诉你哪里不对,卡死只能靠人去 kill。
   *
   * Send a CDP command. The timeout is **not optional**: without it, if the other end
   * of the WebSocket simply stops answering (measured: the page had long since
   * finished and written its results into the DOM, while node waited forever), the
   * returned promise never settles and the whole suite hangs indefinitely without
   * reporting anything. A hang is far worse than a failure: a failure tells you what
   * is wrong, a hang just leaves you killing processes.
   */
  send(method, params = {}, timeoutMs = 20000) {
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        if (this.pending.delete(id)) {
          reject(new Error(`CDP ${method} 超时 / timed out after ${timeoutMs}ms`));
        }
      }, timeoutMs);
      this.pending.set(id, {
        resolve: (v) => { clearTimeout(timer); resolve(v); },
        reject: (e) => { clearTimeout(timer); reject(e); },
      });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }
  /** 在页面里跑一段 JS 并取回结果 / evaluate JS in the page and return the value. */
  async evaluate(expression) {
    const r = await this.send('Runtime.evaluate', {
      expression,
      returnByValue: true,
      awaitPromise: true,
    });
    if (r.exceptionDetails) {
      throw new Error(r.exceptionDetails.text + ' ' +
        (r.exceptionDetails.exception?.description || ''));
    }
    return r.result.value;
  }
}

async function main() {
  const chrome = findChrome();
  const proc = spawn(chrome, [
    '--headless=new',
    '--disable-gpu',
    '--no-sandbox',
    '--hide-scrollbars',
    '--no-first-run',
    '--disable-extensions',
    `--remote-debugging-port=${PORT}`,
    '--window-size=1400,1000',
    'about:blank',
  ], { stdio: ['ignore', 'ignore', 'ignore'] });

  const cleanup = () => { try { proc.kill('SIGKILL'); } catch { /* already gone */ } };
  process.on('exit', cleanup);

  // 等调试端口起来 / wait for the debugging port to come up
  let version = null;
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/json/version`);
      if (r.ok) { version = await r.json(); break; }
    } catch { /* not up yet */ }
    await sleep(250);
  }
  if (!version) { cleanup(); throw new Error('Chrome did not expose a debugging port'); }

  const target = await (await fetch(
    `http://127.0.0.1:${PORT}/json/new?${encodeURIComponent(BASE + PAGE)}`,
    { method: 'PUT' }
  )).json();

  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => {
    ws.addEventListener('open', res, { once: true });
    ws.addEventListener('error', rej, { once: true });
  });

  const cdp = new CDP(ws);
  await cdp.send('Runtime.enable');
  await cdp.send('Page.enable');
  // 收集页面里的报错,测试页会把它们读出来 / collect page errors for the test page to read
  await cdp.evaluate(`
    window.__pageErrors = window.__pageErrors || [];
    window.addEventListener('error', e => window.__pageErrors.push(String(e.message)));
    window.addEventListener('unhandledrejection', e => window.__pageErrors.push('unhandled: ' + String(e.reason)));
    true;
  `);

  // 轮询等结果 / poll for the result
  let raw = null;
  let cdpErrors = 0;
  for (let i = 0; i < 160; i++) {
    try {
      raw = await cdp.evaluate(`
        (function () {
          var el = document.getElementById('JSON');
          return el ? el.textContent : null;
        })()
      `);
      cdpErrors = 0;
    } catch (e) {
      // 页面还在导航时失败几次是正常的;但**连着**失败说明 CDP 这条通道已经不回话
      // 了,继续轮询只会把「卡死」拖成很久的「卡死」。
      //
      // A few failures are normal while the page navigates, but consecutive ones mean
      // the CDP channel has stopped answering; polling on only turns a hang into a long
      // hang.
      if (++cdpErrors >= 3) {
        console.error('❌ CDP 连续失败 / CDP kept failing: ' + e.message);
        break;
      }
    }
    if (raw) break;
    await sleep(250);
  }

  if (!raw) {
    const diagnose = await cdp.evaluate(`
      JSON.stringify({
        readyState: document.readyState,
        hasResults: !!document.getElementById('results'),
        resultRows: document.querySelectorAll('#results div').length,
        body: document.body ? document.body.innerText.slice(0, 600) : ''
      })
    `).catch(e => 'diagnose failed: ' + e.message);
    cleanup();
    console.error('❌ 测试未产出结果 / no result produced:');
    console.error(diagnose);
    process.exit(1);
  }

  ws.close();
  cleanup();

  const data = JSON.parse(raw);
  for (const r of data.results) {
    const tag = r.ok ? '✅' : '❌';
    console.log(`${tag} ${r.name}${r.detail ? '  [' + r.detail + ']' : ''}`);
  }
  console.log('');
  console.log(`通过 / passed: ${data.passed}    失败 / failed: ${data.failed}`);
  process.exit(data.failed === 0 ? 0 : 1);
}

main().catch((e) => {
  console.error('❌ ' + (e.stack || e.message));
  process.exit(2);
});
