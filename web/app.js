/**
 * 解析文案表 / resolve the string table.
 *
 * 服务端会把 {{LANG}} 替换成一个 JSON 对象字面量(不是 JSON 字符串)。但**在
 * Android 上没人替换它** —— APK 里的文件是原样复制的,占位符会原样留着。原来
 * 这里直接 JSON.parse,占位符还在时第一行就抛 SyntaxError,整个脚本加载失败,
 * 界面白屏且没有任何看得见的报错。
 *
 * 所以这里做了三件事:
 *   1. 只要占位符没被替换(还是 {{LANG}}),就当作「没有服务端文案」;
 *   2. 空对象也算没有 —— 某些打包路径会把占位符换成 {} 而不是报错;
 *   3. 兜底用 web/strings.js 里自带的那份,它和界面一起打包,永远在。
 *
 * The server substitutes {{LANG}} with a JSON object literal. **On Android nobody
 * substitutes it** — files inside the APK are copied verbatim and the placeholder
 * survives. The old code called JSON.parse directly, so with the placeholder still
 * present it threw a SyntaxError on the very first line, the whole script failed to
 * load, and the UI went blank with no visible error.
 *
 * Hence three things:
 *   1. a surviving {{LANG}} means "no server strings";
 *   2. an empty object counts as none too — some packaging paths substitute {} rather
 *      than failing;
 *   3. fall back to the copy in web/strings.js, which ships with the UI and is always
 *      there.
 */
function resolveStrings() {
  const builtin = window.PTS_STRINGS || { en: {}, zh: {} };
  // 打包进 APK 时,用系统语言挑一份兜底;挑不到就用英文
  // Inside the APK, pick a fallback by system language; English if nothing matches
  const sys = (navigator.language || 'en').toLowerCase();
  const guess = sys.startsWith('zh') ? 'zh' : 'en';
  let injected = null;

  try {
    const raw = '{{LANG}}';
    // 占位符还在 = 没人替换过 = 打包进 APK 的那条路径
    // The placeholder is still there, so nobody substituted it: the APK path
    if (raw.indexOf('{{LANG}}') === -1) {
      const parsed = JSON.parse(raw.replace(/&quot;/g, '"').replace(/&amp;/g, '&').replace(/&#39;/g, "'"));
      if (parsed && Object.keys(parsed).length > 0) injected = parsed;
    }
  } catch (e) {
    // 替换过但内容坏了:退回自带表,并且留一条痕迹便于排查
    // Substituted but malformed: fall back, and leave a trace for debugging
    console.warn('PrintTheShot: injected string table unusable, using the built-in one', e);
  }

  if (!injected) return Object.assign({}, builtin[guess] || builtin.en, { __code: guess });

  // 服务端的表为准,自带表补齐它没有的键
  // The server's table wins; the built-in one fills in any keys it lacks
  const base = builtin[injected.__code] || builtin.en || {};
  return Object.assign({}, base, injected);
}

const L = resolveStrings();
let currentLang = L.__code || 'en';

/**
 * 补上标题里的版本号 / fill in the version in the page title.
 *
 * index.html 写的是 `<title>PrintTheShot Next v{{VERSION}}</title>`,服务端发页面
 * 时会替换它。但打包进 APK 时文件是原样复制的,没人替换,标题里就留着字面的
 * "{{VERSION}}" —— 和 {{LANG}} 是同一类问题,只是后果轻得多(Android 的 WebView
 * 不显示页面标题,基本看不到)。
 *
 * strings.js 里带了版本号,这里补上。
 *
 * index.html reads <title>PrintTheShot Next v{{VERSION}}</title> and the server
 * substitutes it. Files bundled into the APK are copied verbatim, so nothing does —
 * the literal "{{VERSION}}" stays in the title. Same class as {{LANG}}, far less
 * serious (an Android WebView does not display the page title). strings.js carries
 * the version, so fill it in here.
 */
(function fixTitleVersion() {
  const placeholder = '{{' + 'VERSION}}';
  if (document.title.indexOf(placeholder) === -1) return;
  document.title = document.title.split(placeholder)
    .join(window.PTS_VERSION || '');
})();

// 动态语言切换器(内置 + 自定义) / dynamic language switcher (built-in + custom)
function initLangSwitcher() {
  const langs = L.__languages || [{code: 'en', name: 'English'}, {code: 'zh', name: '中文'}];
  document.getElementById('lang-switch').innerHTML = langs.map(l =>
    `<button class="${l.code === currentLang ? 'active' : ''}" onclick="setLang('${l.code}')">${l.name}</button>`).join('');
}

// AI 翻译设置 / AI translation settings
async function loadAiSettings() {
  const s = await api('/api/settings/ai');
  document.getElementById('ai-enabled').checked = !!s.ai_enabled;
  const keyInput = document.getElementById('ai-key');
  keyInput.placeholder = s.key_set ? '•••••••• (sk-...)' : 'sk-...';
  await loadLanguages();
}

async function saveAiKey() {
  const key = document.getElementById('ai-key').value.trim();
  if (!key) { toast('❌ ' + (L.ai_key_hint || 'enter key')); return; }
  const r = await api('/api/settings/ai', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({key: key}) });
  if (r.success) { toast('✅ ' + (L.ai_key_saved || 'key saved')); document.getElementById('ai-key').value = ''; }
  loadAiSettings();
}

async function toggleAi() {
  const enabled = document.getElementById('ai-enabled').checked;
  const r = await api('/api/settings/ai', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({enabled: enabled}) });
  if (r.success) toast((enabled ? '✅ ' : '') + (L.ai_toggled || 'saved'));
}

async function checkBalance() {
  const el = document.getElementById('ai-balance');
  el.textContent = '...';
  const r = await api('/api/ai/balance');
  el.textContent = r.success ? `💰 ${r.balance} ${r.currency}` : '❌ ' + (r.message || '');
}

async function loadLanguages() {
  const r = await api('/api/languages');
  const el = document.getElementById('lang-list');
  el.innerHTML = r.languages.map(l =>
    l.builtin
      ? `<span class="pager" style="padding:3px 8px;background:#eef1f5;border-radius:6px">${l.name}</span>`
      : `<span class="pager" style="padding:3px 8px;background:#eef1f5;border-radius:6px">${l.name}
           <a href="javascript:void(0)" onclick="deleteLanguage('${l.code}')" style="color:#c0392b;margin-left:4px">✕</a></span>`
  ).join('');
}

// 常用语言预设 / common language presets
const LANG_PRESETS = ['日本語', '한국어', 'Français', 'Deutsch', 'Español', 'Italiano',
                      'Português', 'Русский', 'العربية', 'ไทย', 'Tiếng Việt', 'Türkçe'];

function initLangPresets() {
  document.getElementById('lang-presets').innerHTML = LANG_PRESETS.map(n =>
    `<button class="btn gray" style="padding:3px 10px;font-size:12px" onclick="document.getElementById('lang-name').value='${n}'">${n}</button>`).join('');
}

async function addLanguage() {
  const name = document.getElementById('lang-name').value.trim();
  const note = document.getElementById('ai-note');
  if (!name) { note.textContent = '❌ ' + (L.ai_lang_hint || 'language name required'); return; }
  const btn = document.getElementById('btn-add-lang');
  btn.disabled = true;
  btn.textContent = '⏳';
  note.textContent = '⏳ ' + (L.ai_translating || 'translating UI strings via AI, ~10-30s...');
  const r = await api('/api/languages', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({name: name}) })
    .catch(e => ({ success: false, message: String(e) }));
  btn.disabled = false;
  btn.textContent = L.btn_add_lang || 'Add';
  note.textContent = (r.success ? '✅ ' : '❌ ') + (r.message || '');
  if (r.success) setTimeout(() => location.reload(), 900);
}

// 翻译当前曲线:先选语言,再翻译重渲染,完成后弹大图
let pendingTranslateFn = '';

async function translateShot(filename) {
  pendingTranslateFn = filename;
  // 语言下拉:内置 + 自定义;留空输入框填其他语言
  const sel = document.getElementById('t-lang');
  const langs = L.__languages || [{code:'en',name:'English'},{code:'zh',name:'中文'}];
  sel.innerHTML = langs.map(l => `<option value="${l.code}">${l.name}</option>`).join('');
  document.getElementById('t-lang-custom').value = '';
  document.getElementById('t-note').textContent = '';
  document.getElementById('t-title').textContent = '🌐 ' + (L.t_title || 'Translate into...');
  document.getElementById('t-go').textContent = L.btn_translate || 'Translate';
  document.getElementById('translate-modal').classList.add('show');
}

function closeTranslateModal() {
  document.getElementById('translate-modal').classList.remove('show');
}

async function doTranslate() {
  const sel = document.getElementById('t-lang');
  const custom = document.getElementById('t-lang-custom').value.trim();
  let lang = sel.value;
  const note = document.getElementById('t-note');
  const btn = document.getElementById('t-go');
  btn.disabled = true;
  btn.textContent = '⏳';
  // 自定义语言:先自动添加(如果还没注册) / auto-add custom language first
  if (custom) {
    note.textContent = '⏳ ' + (L.ai_translating || 'translating...');
    const ar = await api('/api/languages', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({name: custom}) })
      .catch(e => ({ success: false, message: String(e) }));
    if (!ar.success) { note.textContent = '❌ ' + (ar.message || ''); btn.disabled = false; btn.textContent = '🌐'; return; }
    // 从新增的语言里找对应的code
    const ls = await api('/api/languages');
    const added = ls.languages.find(l => l.name === custom);
    lang = added ? added.code : lang;
  }
  const r = await api('/api/translate/shot', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({filename: pendingTranslateFn, lang: lang}) })
    .catch(e => ({ success: false, message: String(e) }));
  btn.disabled = false;
  btn.textContent = '🌐';
  if (!r.success) { note.textContent = '❌ ' + (r.message || ''); return; }
  closeTranslateModal();
  // 数据变了 → 缓存作废 → 卡片和大图重画一遍。
  // 过去这里要重新向服务端要一张 PNG,现在只是本地重画,没有等待。
  //
  // Data changed, so drop the cache and redraw the card and the large view. This
  // used to fetch a freshly rendered PNG from the server; now it is a local
  // redraw with nothing to wait for.
  PrintTheShotPrinter.clearShotCache(pendingTranslateFn);
  viewImage(pendingTranslateFn);
  loadShots();
}

async function deleteLanguage(code) {
  const r = await api('/api/languages/delete', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({code: code}) });
  if (r.success) location.reload();
}

function T(key) { return L[key] || key; }

function toast(msg) {
  const el = document.getElementById('toast');
  el.textContent = msg; el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 2200);
}

/**
 * 所有 API 调用的唯一入口。
 *
 * 必须走 PrintTheShotPrinter.apiUrl() 把路径补成绝对地址 —— 在 Android 上 Web UI
 * 是打包在 APK 里的,相对路径会打到 WebView 自己身上而不是局域网服务端。
 * 浏览器里 apiUrl() 原样返回,行为和以前一致。
 *
 * The single entry point for every API call.
 *
 * It must go through PrintTheShotPrinter.apiUrl() to turn the path into an absolute
 * URL: on Android the web UI is bundled inside the APK, so a relative path would hit
 * the WebView itself rather than the server on the LAN. In a browser apiUrl()
 * returns the path unchanged, so behaviour there is exactly as before.
 */
async function api(path, opts) {
  const r = await fetch(PrintTheShotPrinter.apiUrl(path), opts);
  return r.json();
}

/** 需要绝对地址的链接(下载、插件)也要过一遍 / links needing absolute URLs go through it too. */
function url(path) {
  return PrintTheShotPrinter.apiUrl(path);
}

// ---------------------------------------------------------------------------
// 首次启动:配置服务端地址 / first run: configure the server address
// ---------------------------------------------------------------------------
// 只在 Android 上会走到这里。浏览器里 Web UI 就是从服务端拿的,天然知道地址;
// APK 里 Web UI 是打包进去的,必须问用户服务端在哪。
//
// 这一屏不做成「设置里的一项」而是启动就拦,是因为不配的话整个界面什么都显示不
// 出来 —— 让用户自己去设置页找一个能修好白屏的开关,不如直接把问题摆出来。
//
// Only reached on Android. In a browser the web UI came from the server so the
// address is known; inside the APK the UI is bundled and the server has to be asked
// for.
//
// It gates startup rather than living in settings because without it nothing renders
// at all: making the user hunt through a settings page for the switch that fixes a
// blank screen is worse than stating the problem outright.

const SERVER_PRESETS = [
  { host: location.hostname && location.hostname !== 'localhost' ? location.hostname : '', port: 8000 }
];

function showServerSetup() {
  const overlay = document.createElement('div');
  overlay.className = 'lightbox show';
  overlay.id = 'server-setup';
  overlay.innerHTML = `
    <div class="lb-wrap t-modal" style="max-width:420px">
      <h3 style="margin-bottom:6px;color:#333">${T('setup_title')}</h3>
      <p style="font-size:13px;color:var(--muted);margin-bottom:12px">${T('setup_hint')}</p>
      <input id="setup-url" placeholder="192.168.1.100:8000" autocomplete="off"
             style="width:100%;padding:10px;border:1px solid var(--border);border-radius:6px;box-sizing:border-box;font-size:14px">
      <div style="display:flex;gap:8px;margin-top:12px">
        <button class="btn" id="setup-go">${T('setup_connect')}</button>
      </div>
      <div class="note" id="setup-note" style="margin-top:10px"></div>
    </div>`;
  document.body.appendChild(overlay);

  const input = overlay.querySelector('#setup-url');
  const note = overlay.querySelector('#setup-note');
  const btn = overlay.querySelector('#setup-go');
  input.focus();

  async function attempt() {
    const raw = input.value.trim();
    if (!raw) { note.textContent = '❌ ' + T('setup_required'); return; }
    btn.disabled = true;
    btn.textContent = '⏳';
    note.textContent = '⏳ ' + T('setup_testing');
    // 先存下来再测:apiUrl() 是从存储里读地址的
    // Store first, then test: apiUrl() reads the address from storage
    PrintTheShotPrinter.setServerBase(raw);
    try {
      const r = await fetch(PrintTheShotPrinter.apiUrl('/api/status'));
      const j = await r.json();
      if (!j || !j.version) throw new Error('unexpected response');
      note.textContent = '✅ ' + T('setup_ok').replace('{v}', j.version);
      setTimeout(() => location.reload(), 700);
    } catch (e) {
      // 连不上就把地址清掉,免得留下一个半好不好的配置
      // Clear it on failure rather than leaving a half-working configuration behind
      PrintTheShotPrinter.setServerBase('');
      note.textContent = '❌ ' + T('setup_failed') + ' ' + (e.message || '');
      btn.disabled = false;
      btn.textContent = T('setup_connect');
    }
  }

  btn.addEventListener('click', attempt);
  input.addEventListener('keydown', e => { if (e.key === 'Enter') attempt(); });
}

// 已经配好了,但想在设置里改 / already configured, but let the user change it
function changeServer() {
  const cur = PrintTheShotPrinter.getServerBase();
  const next = prompt(T('setup_prompt'), cur);
  if (next === null) return;
  PrintTheShotPrinter.setServerBase(next);
  location.reload();
}

async function loadStatus() {
  const s = await api('/api/status');
  document.getElementById('subtitle').textContent = `${T('status_running')} · v${s.version}`;
  const items = [
    [T('status_running'), s.status, ''],
    [T('start_time'), s.start_time, ''],
    [T('shots_received'), s.shots_received, ''],
    [T('active_users'), s.active_users, ''],
    [T('max_users'), s.max_users, ''],
    [T('print_enabled'), s.print_enabled ? '✅' : '🚫', s.print_enabled ? 'ok' : 'off'],
    [T('bean_info_enabled'), s.bean_info_enabled ? '✅' : '🚫', s.bean_info_enabled ? 'ok' : 'off'],
  ];
  // AI余额(配置了key才显示) / AI balance (shown when a key is set)
  try {
    const ai = await api('/api/settings/ai');
    if (ai.key_set) {
      const bal = await api('/api/ai/balance');
      items.push([T('stat_ai_balance'),
        bal.success ? `💰 ${bal.balance} ${bal.currency}` : '—', '']);
    }
  } catch (e) {}
  const grid = document.getElementById('status-grid');
  grid.innerHTML = items.map(([label, value, cls]) =>
    `<div class="stat"><div class="label">${label}</div><div class="value ${cls}">${value}</div></div>`).join('');
  // 只有 macOS 打包版才需要这个按钮(见服务端 show_stop_button 的说明)
  // Only a packaged macOS build needs this button; see show_stop_button
  document.getElementById('btn-shutdown').style.display = s.show_stop_button ? '' : 'none';
  document.getElementById('btn-print').textContent = s.print_enabled ? T('disable_print') : T('enable_print');
  document.getElementById('btn-print').classList.toggle('green', !s.print_enabled);
  document.getElementById('btn-bean').textContent = s.bean_info_enabled ? T('disable_bean_info') : T('enable_bean_info');
  document.getElementById('btn-bean').classList.toggle('gray', s.bean_info_enabled);
  document.getElementById('btn-bean').classList.toggle('green', !s.bean_info_enabled);
  document.getElementById('btn-print').classList.toggle('gray', s.print_enabled);
}

async function loadQueue() {
  const q = await api('/api/queue');
  document.getElementById('h-queue').textContent = T('queue_status').replace('{count}', q.count);
  const el = document.getElementById('queue-list');
  el.innerHTML = q.count === 0 ? `<div class="empty">${T('queue_empty')}</div>` : q.jobs.map(j =>
    `<div>${j.time} · ${j.filename} <span class="${j.ok ? 'ok' : 'fail'}">${j.ok ? '✓' : '✗'}</span></div>`).join('');
}

let availableDates = [];
let currentDate = '';   // 初始空 → 首次加载取今天(无数据则最新日期); 'all' = 全部日期(上限100)
let dateInitialized = false;
let currentPage = 0;
let pageSize = 9;
try { pageSize = parseInt(localStorage.getItem('pts_page_size')) || 9; } catch (e) {}

async function loadShots() {
  const d = await api('/api/shots' + (currentDate && currentDate !== 'all' ? '?date=' + currentDate : ''));
  availableDates = d.dates;
  // 首次:默认选今天(有数据时),否则最新日期
  if (!dateInitialized) {
    dateInitialized = true;
    const now = new Date();
    const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
    currentDate = availableDates.includes(today) ? today : (availableDates[0] || 'all');
  }
  // 日期下拉:各日期 + 全部日期(默认=当前日期)
  const sel = document.getElementById('date-filter');
  sel.innerHTML = d.dates.map(x => `<option value="${x}">${x}</option>`).join('')
    + `<option value="all">${T('all_dates')}</option>`;
  sel.value = currentDate;
  // 每页数量(最左边)
  const psSel = document.getElementById('page-size');
  psSel.innerHTML = [9, 18, 36].map(n =>
    `<option value="${n}" ${n === pageSize ? 'selected' : ''}>${n} ${T('per_page')}</option>`).join('');

  // 当前页(最右边)
  const total = d.shots.length;
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (currentPage >= pages) currentPage = pages - 1;
  if (currentPage < 0) currentPage = 0;
  const slice = d.shots.slice(currentPage * pageSize, (currentPage + 1) * pageSize);

  const grid = document.getElementById('shot-grid');
  window.chartLangs = window.chartLangs || {};
  if (!slice.length) { grid.innerHTML = `<div class="empty" style="grid-column:1/-1">${T('no_data')}</div>`; }
  else {
    // 缩略图改成 canvas:服务端不再提供 PNG,卡片里这张图是前端现画的。
    // 也正因为如此,切换语言不需要重新向服务端要图 —— 重画一遍就行。
    //
    // Thumbnails are canvases now: the server serves no PNGs, so each card draws
    // its own chart. It also means switching language needs no round-trip for a
    // new image — redrawing is enough.
    grid.innerHTML = slice.map(s => `<div class="shot">
        <canvas class="thumb" data-fn="${s.filename}" width="1296" height="576"
                title="${T('view_large')}" onclick="viewImage('${s.filename}')"></canvas>
        <div class="meta"><b>${(s.bean ? s.bean + ' - ' : '') + (s.profile || '?')}</b>${s.machine_id} · ${s.timestamp} · ${(s.data_size/1024).toFixed(1)}KB</div>
        <div class="actions">
          <button class="btn" onclick="printShot('${s.filename}')" title="${T('print')}">🖨️</button>
          <a class="btn gray" href="${url('/download/json/' + s.filename)}" download title="JSON">📄</a>
          <button class="btn gray" onclick="downloadShotPng('${s.filename}')" title="PNG">🖼️</button>
          <button class="btn gray" onclick="translateShot('${s.filename}')" title="${T('btn_translate')}">🌐</button>
        </div>
      </div>`).join('');

    // 懒加载:滚进视口才画。一页 36 张全部立刻渲染会明显拖慢翻页,
    // 而用户一次其实只看得到 6 张。
    // Lazy: only draw what scrolls into view. Rendering all 36 immediately makes
    // paging visibly sluggish, and the user can only see about six at a time.
    Array.from(grid.querySelectorAll('canvas.thumb')).forEach(cv => {
      if (thumbObserver) thumbObserver.observe(cv);
      else drawThumb(cv);
    });
  }
  document.getElementById('page-info').textContent =
    `${currentPage + 1}/${pages} · ${T('total_n').replace('{n}', total)}`;
  document.getElementById('btn-page-prev').disabled = currentPage <= 0;
  document.getElementById('btn-page-next').disabled = currentPage >= pages - 1;

  // 前后日(中间)
  const idx = availableDates.indexOf(currentDate);
  const prevBtn = document.getElementById('btn-prev');
  const nextBtn = document.getElementById('btn-next');
  prevBtn.title = T('prev_day'); nextBtn.title = T('next_day');
  if (currentDate === 'all') { prevBtn.disabled = true; nextBtn.disabled = true; }
  else { prevBtn.disabled = idx <= 0; nextBtn.disabled = idx >= availableDates.length - 1; }
}

function shiftPage(dir) {
  currentPage += dir;
  loadShots();
}

function setPageSize() {
  pageSize = parseInt(document.getElementById('page-size').value);
  try { localStorage.setItem('pts_page_size', pageSize); } catch (e) {}
  currentPage = 0;
  loadShots();
}

function shiftDate(dir) {
  if (currentDate === '' || currentDate === 'all') {
    if (availableDates.length) { currentDate = availableDates[0]; loadShots(); }
    return;
  }
  const idx = availableDates.indexOf(currentDate);
  const next = availableDates[idx + dir];
  if (next) { currentDate = next; loadShots(); }
}

async function loadStats() {
  const s = await api('/api/stats');
  const grid = document.getElementById('stats-grid');
  grid.innerHTML = [
    [T('stat_total'), s.total_shots],
    [T('stat_dates'), s.total_dates],
    [T('stat_machines'), s.machines],
    [T('stat_avg_size'), (s.avg_data_size/1024).toFixed(0) + 'KB'],
    [T('stat_prints'), s.prints_session],
  ].map(([label, value]) =>
    `<div class="stat"><div class="label">${label}</div><div class="value">${value}</div></div>`).join('');
  // 三列分布:日期(围绕均值发散) | 冲煮方案(对数+颜色) | 豆子(围绕均值发散)
  const bars = document.getElementById('stats-bars');
  const header = t => `<div style="font-size:12px;color:var(--muted);margin:0 0 6px">${t}</div>`;
  const rowHtml = (label, count, extra) =>
    `<div class="bar-row"><span class="bar-label" title="${label}">${label}</span>${extra}<span>${count}</span></div>`;

  // 列1: 日期分布,围绕日均值上下浮动(发散式bar,颜色分方向)
  const dayCounts = s.per_date.map(p => p.count);
  const avg = dayCounts.length ? dayCounts.reduce((a, b) => a + b, 0) / dayCounts.length : 0;
  const maxDev = Math.max(1, ...dayCounts.map(c => Math.abs(c - avg)));
  const divBar = c => {
    const dev = c - avg;
    const w = Math.max(4, Math.round(Math.abs(dev) / maxDev * 60));
    const color = dev >= 0 ? 'rgb(70,170,255)' : 'rgb(255,150,90)';
    const cls = dev >= 0 ? 'up' : 'down';
    return `<div class="div-wrap"><div class="div-center"></div><div class="bar ${cls}" style="width:${w}px;background:${color}"></div></div>`;
  };
  let dateHtml = header(T('stat_per_date') + (avg ? ` (${T('stat_avg_day').replace('{n}', Math.round(avg))})` : ''));
  dateHtml += s.per_date.map(p => rowHtml(p.date, p.count, divBar(p.count))).join('');
  if (s.older_days > 0) {
    const olderAvg = Math.round(s.older_count / s.older_days);
    dateHtml += rowHtml(T('stat_older').replace('{n}', s.older_days), `${s.older_count} (${T('stat_avg_day').replace('{n}', olderAvg)})`,
      `<div class="div-wrap"><div class="div-center"></div><div class="bar" style="width:4px;background:#c3c8cf"></div></div>`);
  }

  // 列2: 冲煮方案(对数压缩 + 颜色深浅,独立比例)
  const profCol = s.top_profiles.length ? (() => {
    const pmax = Math.max(1, ...s.top_profiles.map(p => p.count));
    const logW = c => Math.round(Math.log2(c + 1) / Math.log2(pmax + 1) * 110);
    const barColor = c => { const r = c / pmax; return `rgb(70, ${Math.round(120 + r * 135)}, 255)`; };
    let h = header(T('stat_profiles'));
    h += s.top_profiles.map(p =>
      rowHtml(p.name, p.count, `<div class="bar" style="width:${logW(p.count)}px;background:${barColor(p.count)}"></div>`)).join('');
    return h;
  })() : '';

  // 列3: 豆子分布(围绕平均份额发散,与日期同视觉语言)
  const beanCol = s.top_beans.length ? (() => {
    const bCounts = s.top_beans.map(p => p.count);
    const bAvg = bCounts.reduce((a, b) => a + b, 0) / bCounts.length;
    const bMaxDev = Math.max(1, ...bCounts.map(c => Math.abs(c - bAvg)));
    const bDiv = c => {
      const dev = c - bAvg;
      const w = Math.max(4, Math.round(Math.abs(dev) / bMaxDev * 60));
      const color = dev >= 0 ? 'rgb(70,170,255)' : 'rgb(255,150,90)';
      const cls = dev >= 0 ? 'up' : 'down';
      return `<div class="div-wrap"><div class="div-center"></div><div class="bar ${cls}" style="width:${w}px;background:${color}"></div></div>`;
    };
    let h = header(T('stat_beans'));
    h += s.top_beans.map(p => rowHtml(p.name, p.count, bDiv(p.count))).join('');
    return h;
  })() : '';

  bars.innerHTML = `<div class="stats-cols"><div>${dateHtml}</div><div>${profCol}</div><div>${beanCol}</div></div>`;
}

// ---------- Canvas 绘制辅助 / Canvas drawing helpers ----------

/**
 * 画一张缩略图。用 IntersectionObserver 做懒加载:滚进视口才画,不然一页 36 张
 * 会白白渲染掉。
 *
 * Draw one thumbnail. An IntersectionObserver lazily renders only what is scrolled
 * into view — otherwise every one of the 36 cards on a page is rendered for nothing.
 */
const thumbObserver = ('IntersectionObserver' in window) ? new IntersectionObserver(entries => {
  entries.forEach(async e => {
    if (!e.isIntersecting) return;
    thumbObserver.unobserve(e.target);
    await drawThumb(e.target);
  });
}, { rootMargin: '200px' }) : null;

async function drawThumb(canvas) {
  const filename = canvas.dataset.fn;
  try {
    const shot = await PrintTheShotPrinter.loadShot(filename, currentLang);
    const res = PrintTheShotRender.renderShotToCanvas(shot, canvas, { lang: currentLang, scale: 1 });
    if (!res.ok) throw new Error(res.error);
    canvas.classList.add('ready');
  } catch (err) {
    const holder = canvas.parentElement;
    if (holder) canvas.outerHTML = `<div class="empty">⚠️ ${(err.message || 'render failed')}</div>`;
  }
}

// ---------- 打印 / Printing ----------

/**
 * 打印一条 shot。渲染 → 位图 → 走平台打印路径(HTTP 或 Android 原生蓝牙)。
 * Print a shot: render → bitmap → the platform's print path.
 */
async function printShot(filename) {
  const btn = event && event.target;
  if (btn) btn.disabled = true;
  try {
    const r = await PrintTheShotPrinter.printShot(filename, { lang: currentLang });
    toast(r.success ? (T('print_job_sent') + ' · ' + r.via) : ('❌ ' + r.message));
  } finally {
    if (btn) btn.disabled = false;
  }
}

/** 把一条 shot 画出来另存为 PNG(服务端不再生成 PNG,由前端导出)。 */
async function downloadShotPng(filename) {
  try {
    const bitmap = await PrintTheShotPrinter.shotToBitmap(filename, { rotate: false, lang: currentLang });
    // 用一张临时 canvas 把 1-bit 数据还原成可下载的 PNG
    // Rebuild the 1-bit data into a temporary canvas so it can be saved as a PNG
    const c = document.createElement('canvas');
    c.width = bitmap.width; c.height = bitmap.height;
    const ctx = c.getContext('2d');
    ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, c.width, c.height);
    const img = ctx.createImageData(bitmap.width, bitmap.height);
    for (let y = 0; y < bitmap.height; y++) {
      for (let x = 0; x < bitmap.width; x++) {
        const bit = (bitmap.data[y * bitmap.bytesPerRow + (x >> 3)] >> (7 - (x & 7))) & 1;
        const o = (y * bitmap.width + x) * 4;
        const v = bit ? 0 : 255;
        img.data[o] = img.data[o+1] = img.data[o+2] = v;
        img.data[o+3] = 255;
      }
    }
    ctx.putImageData(img, 0, 0);
    const a = document.createElement('a');
    a.download = filename.replace('.json', '.png');
    a.href = c.toDataURL('image/png');
    a.click();
  } catch (e) {
    toast('❌ ' + (e.message || e));
  }
}

// ---------- 大图 / Lightbox ----------
// 点击缩略图查看大图(不再触发打印) / click a thumbnail for the large view

let lightboxFilename = '';

async function viewImage(filename) {
  lightboxFilename = filename;
  const canvas = document.getElementById('lightbox-canvas');
  document.getElementById('lb-json').href = url('/download/json/' + filename);
  // 语言chips:切换大图到其它语言 / language chips: switch the large view
  const langs = L.__languages || [{code:'en',name:'English'},{code:'zh',name:'中文'}];
  const cur = currentLang;
  document.getElementById('lb-langs').innerHTML = langs.map(l =>
    `<button class="btn gray" style="padding:3px 10px;font-size:12px${l.code === cur ? ';background:var(--accent);color:#fff' : ''}"
      onclick="switchLightboxLang('${l.code}')">${l.name}</button>`).join('');
  document.getElementById('lightbox').classList.add('show');

  try {
    const shot = await PrintTheShotPrinter.loadShot(filename, cur);
    const res = PrintTheShotRender.renderShotToCanvas(shot, canvas, { lang: cur, scale: 1 });
    if (!res.ok) throw new Error(res.error);
  } catch (e) {
    toast('❌ ' + (e.message || e));
  }
}

// 大图切换语言:渲染在本地发生,数据换一份重画即可,不用等服务端出图。
// Switching language re-renders locally from freshly loaded data — no waiting on
// the server to produce an image.
async function switchLightboxLang(lang) {
  if (!lightboxFilename) return;
  const r = await api('/api/translate/shot', { method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({filename: lightboxFilename, lang: lang, allow_api: false}) })
    .catch(e => ({ success: false, message: String(e) }));
  if (!r.success) { toast('❌ ' + (r.message || '')); return; }
  PrintTheShotPrinter.clearShotCache(lightboxFilename);
  viewImage(lightboxFilename);
}

function closeLightbox() {
  document.getElementById('lightbox').classList.remove('show');
}

// 大图操作栏:打印 / print the shot shown in the lightbox
async function printLightbox() {
  if (!lightboxFilename) return;
  const r = await PrintTheShotPrinter.printShot(lightboxFilename, { lang: currentLang });
  toast(r.success ? (T('print_job_sent') + ' · ' + r.via) : ('❌ ' + r.message));
}

document.addEventListener('keydown', e => { if (e.key === 'Escape') closeLightbox(); });

/**
 * 停止服务。
 *
 * 两件事要说清楚:
 *
 *   1. **二次确认不能省。** 这是唯一一个会让当前页面立刻失效的操作,误点一次
 *      就得去重新启动应用。
 *   2. **服务停掉之后这个页面就"死"了** —— 后续所有轮询都会失败,页面上会冒出
 *      一堆错误提示。所以停之前先把定时刷新关掉,再把界面切到一个明确的"已停止"
 *      状态,而不是让用户对着一堆报错猜发生了什么。
 *
 * 服务端是先回响应再真正停止的,所以这里能正常收到 200。
 *
 * Stopping the service.
 *
 * Two things worth being explicit about:
 *
 *   1. **The confirmation is not optional.** It is the only action that immediately
 *      invalidates the page you are looking at.
 *   2. **Once stopped, this page is dead** — every subsequent poll fails and the UI
 *      fills with error messages. So the refresh timer is cleared first and the page
 *      is switched to an explicit "stopped" state, rather than leaving the user to
 *      guess what happened from a wall of red.
 *
 * The server answers before it stops, which is why a 200 arrives here.
 */
async function stopService() {
  if (!confirm(T('shutdown_confirm'))) return;
  const btn = document.getElementById('btn-shutdown');
  btn.disabled = true;
  btn.textContent = '⏳ ' + T('btn_shutdown');
  try {
    const r = await api('/api/shutdown', { method: 'POST' });
    if (!r.success) {
      alert('❌ ' + (r.message || T('shutdown_failed')));
      btn.disabled = false;
      btn.textContent = '⏻ ' + T('btn_shutdown');
      return;
    }
  } catch (e) {
    // 服务可能已经停了、连接被拒 —— 那其实说明操作成功了
    // The service may already be down and the connection refused — which means it
    // actually worked
  }
  clearInterval(window.__refreshTimer);
  document.querySelector('main').innerHTML =
    '<div class="card" style="text-align:center;padding:48px 24px">'
    + '<div style="font-size:40px">⏻</div>'
    + '<h2 style="justify-content:center;margin-top:12px">' + T('shutdown_ok') + '</h2>'
    + '<p class="note" style="margin-top:8px">' + T('shutdown_sent') + '</p>'
    + '</div>';
}

async function togglePrint() {
  const s = await api('/api/status');
  await api('/api/settings/print', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({enabled: !s.print_enabled}) });
  loadStatus();
}

async function toggleBean() {
  const s = await api('/api/status');
  await api('/api/settings/beaninfo', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({enabled: !s.bean_info_enabled}) });
  loadStatus();
}

async function clearQueue() {
  await api('/api/queue', { method: 'DELETE' });
  toast(T('clear_queue') + ' ✓');
  loadQueue();
}


async function checkUpdate() {
  const btn = document.getElementById('btn-check-update');
  btn.disabled = true;
  const r = await api('/api/update/check').catch(e => ({ error: String(e) }));
  btn.disabled = false;
  const note = document.getElementById('update-note');
  if (r.error) { note.textContent = '❌ ' + r.error; return; }
  const state = r.update_available ? T('update_avail') : T('update_ok');
  note.textContent = T('update_check').replace('{local}', r.local).replace('{remote}', r.remote) + ' · ' + state;
  document.getElementById('btn-update-service').disabled = !r.update_available;
}

async function updateService() {
  const btn = document.getElementById('btn-update-service');
  btn.disabled = true;
  const r = await api('/api/update', { method: 'POST' }).catch(e => ({ success: false, message: String(e) }));
  toast((r.success ? '🔄 ' : '❌ ') + (r.message || ''));
  btn.disabled = false;
}

function showSettings() { document.getElementById('main-view').style.display = 'none'; document.getElementById('settings-view').style.display = 'block'; }
function hideSettings() { document.getElementById('settings-view').style.display = 'none'; document.getElementById('main-view').style.display = 'block'; }

async function setLang(lang) {
  await api('/api/language', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({language: lang}) });
  location.reload();
}

function refreshAll() { loadStatus(); loadQueue(); loadShots(); loadStats(); }

// 拖放上传
const dz = document.getElementById('dropzone');
dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('drag'); });
dz.addEventListener('dragleave', () => dz.classList.remove('drag'));
dz.addEventListener('drop', e => {
  e.preventDefault(); dz.classList.remove('drag');
  if (e.dataTransfer.files.length) uploadFile(e.dataTransfer.files[0]);
});
document.getElementById('drop-file').addEventListener('change', e => {
  if (e.target.files.length) uploadFile(e.target.files[0]);
  e.target.value = '';
});

async function uploadFile(file) {
  const text = await file.text();
  const r = await fetch('/upload', { method: 'POST', headers: {'Content-Type':'application/json'}, body: text });
  const j = await r.json();
  toast(j.status === 'success' ? `${T('upload_success')}: ${j.message}` : '❌ ' + (j.message || j.error || ''));
  setTimeout(refreshAll, 1500);
}

// 初始化文案
function initText() {
  document.getElementById('title').textContent = T('server_title').replace('{VERSION}', L.__version || '');
  document.getElementById('h-status').textContent = '📊 ' + T('print_control');
  document.getElementById('h-queue').textContent = T('print_queue');
  document.getElementById('h-upload').textContent = '📤 ' + T('data_upload');
  document.getElementById('h-recent').textContent = '📈 ' + T('recent_data');
  document.getElementById('h-stats').textContent = '📊 ' + T('h_stats');
  document.getElementById('h-plugin').textContent = '📥 ' + T('plugin_download');
  document.getElementById('btn-clear').textContent = T('clear_queue');
  document.getElementById('btn-refresh').textContent = '↻ ' + T('refresh');
  document.getElementById('btn-shutdown').textContent = '⏻ ' + T('btn_shutdown');
  document.getElementById('btn-plugin').textContent = T('plugin_local');
  document.getElementById('btn-plugin-github').textContent = T('plugin_github');
  document.getElementById('btn-plugin-txt').textContent = T('plugin_txt');
  document.getElementById('plugin-note').textContent = T('plugin_note');
  document.getElementById('h-update').textContent = '🔄 ' + T('update_title');
  document.getElementById('h-ai-settings').textContent = '🤖 ' + T('h_ai_settings');
  document.getElementById('btn-save-key').textContent = T('btn_save_key');
  document.getElementById('btn-ai-balance').textContent = T('btn_check_balance');
  document.getElementById('ai-enabled-label').textContent = T('ai_enabled_label');
  document.getElementById('h-languages').textContent = T('h_languages');
  document.getElementById('btn-add-lang').textContent = T('btn_add_lang');
  document.getElementById('btn-check-update').textContent = T('btn_check_update');
  document.getElementById('btn-update-service').textContent = T('btn_update_service');
  document.getElementById('update-note').textContent = T('update_note');
  document.getElementById('dropzone').textContent = T('drag_drop');
  document.getElementById('step1').textContent = T('plugin_step1');
  document.getElementById('step2').textContent = T('plugin_step2');
  document.getElementById('step3').textContent = T('plugin_step3');
  document.getElementById('step4').textContent = T('plugin_step4');
  const hPrint = document.getElementById('h-print');
  if (hPrint) hPrint.textContent = T('h_print');
}

initText();
initLangSwitcher();
initLangPresets();
loadAiSettings();
refreshAll();
window.__refreshTimer = setInterval(refreshAll, 8000);

// ---------- 打印初始化 / Printing bootstrap ----------
// 上传到达的 shot 由前端这边渲染并打印(服务端手里没有图),所以这里要起一个
// 轮询把待打印队列拉下来。间隔 5 秒 —— 足够及时,又不至于把服务端问烦。
//
// Arriving shots are rendered and printed by this end (the server has no image),
// so a poll drains the pending-print queue. Five seconds is prompt enough without
// pestering the server.
PrintTheShotRender.ensureFontReady().then(() => {
  PrintTheShotPrinter.startAutoPrint(5000);
  initPrintSettings();
  // 蓝牙设置卡片只在 Android 上有内容,桌面浏览器里 bt.js 会把它隐藏掉
  // The Bluetooth card only has content on Android; bt.js hides it in a browser
  if (window.PrintTheShotBluetooth) PrintTheShotBluetooth.mount();
});

/** 打印设置卡片:显示平台、打印机、模式 / the print settings card. */
async function initPrintSettings() {
  const card = document.getElementById('print-card');
  if (!card) return;
  const [cfg, list] = await Promise.all([
    PrintTheShotPrinter.getConfig(true),
    PrintTheShotPrinter.listPrinters()
  ]);

  const options = (list.printers || []).map(p =>
    `<option value="${p.id || p.address}" ${(p.default || cfg.printer === (p.id || p.address)) ? 'selected' : ''}>${p.name || p.id}</option>`
  ).join('');

  const modes = [
    ['', 'auto'],
    ['driver', 'driver (CUPS)'],
    ['raw', 'raw (ESC/POS)'],
    ['escpos', 'escpos (Windows)'],
    ['bmp', 'bmp (legacy)']
  ];

  card.innerHTML = `
    <div class="stat"><div class="label">${T('print_platform')}</div>
      <div class="value ${cfg.available ? 'ok' : 'off'}">${cfg.platform_name || cfg.platform}${cfg.available ? '' : ' ⚠️'}</div></div>
    <div class="stat"><div class="label">${T('print_transport')}</div>
      <div class="value">${PrintTheShotPrinter.transport()}</div></div>
    <div class="stat"><div class="label">${T('print_printer')}</div>
      <div class="value">
        <select id="printer-select" onchange="savePrintSetting('printer', this.value)" style="font-size:12px;max-width:200px">
          <option value="">${T('print_default')}</option>${options}
        </select>
      </div></div>
    <div class="stat"><div class="label">${T('print_mode')}</div>
      <div class="value">
        <select id="print-mode" onchange="savePrintSetting('mode', this.value)" style="font-size:12px">
          ${modes.map(([v, l]) => `<option value="${v}" ${(cfg.mode || '') === v ? 'selected' : ''}>${l}</option>`).join('')}
        </select>
      </div></div>
    <div class="stat"><div class="label">${T('print_width')}</div>
      <div class="value">
        <select id="print-width" onchange="savePrintSetting('print_width', parseInt(this.value,10))" style="font-size:12px">
          ${[576, 512, 384].map(w => `<option value="${w}" ${cfg.print_width === w ? 'selected' : ''}>${w} dots</option>`).join('')}
        </select>
      </div></div>
  `;
}

/** 保存一项打印设置。改完立刻生效 —— 服务端会重新装载适配器。 */
async function savePrintSetting(key, value) {
  const r = await PrintTheShotPrinter.saveConfig({ [key]: value });
  if (r && r.success) toast(T('print_saved'));
  else toast('❌ ' + ((r && r.message) || 'save failed'));
}
