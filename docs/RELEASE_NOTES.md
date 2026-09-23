# PrintTheShot Next v2.1-beta.3

---

# English

The release that made the Android app usable as a background print node. Almost
everything here comes from one fact: a tablet whose app is not in the foreground is
**frozen by the system**, and the whole chain fails silently when it is.

### Changed

- **The background service is no longer optional, and its type changed.** Nothing in
  the app ever called `startForegroundService` — it existed in the plugin and in a doc
  comment and nowhere else, so the service could never start and the app was frozen the
  moment it went to the background. It now starts with the app, and there is a switch in
  the UI to turn it off.
- **`dataSync` → `connectedDevice`.** A Bluetooth printer is an external device, which
  is what `connectedDevice` means; more to the point, from Android 15 `dataSync` carries
  a 6-hour-per-24-hour cap and is stopped by the system when it expires, while
  `connectedDevice` is not among the types that limit applies to. Reading the job as
  "moving data" had quietly ruled out the only shape this service makes sense in.

### Fixed

- **Printing stopped in the background, and the queue was never drained.** The pump was
  a `setInterval` inside the WebView; once the WebView is hidden, Chromium throttles its
  timers to roughly once a minute and sometimes not at all. The server now pokes the
  front end when a shot arrives — an `evaluateJavascript` call, not a timer, so the
  throttling does not apply. Rendering still happens only in the front end.
- **The same receipt could print forever.** When a print succeeded but its
  acknowledgement did not arrive, the server kept the job and the next pass claimed it
  again. This was hit for real: the printer fed paper continuously. "Print each file
  once" is now this end's own invariant — recorded *before* the ack, so a lost ack
  cannot reprint it — with a per-pass cap as a backstop.
- **The same shot printed several times.** The DE1's `after_flow_complete` can fire more
  than once, and an upload that times out client-side still lands on the server, so the
  retry stores a second copy. Every upload gets a fresh filename, so name-based
  de-duplication caught none of it. De-duplication is now by **content** (SHA-256, 30
  second window) on both the Android and the desktop server, and the front end re-fetches
  the queue per job so the copies the server drops are dropped before they print.
- **The machine name printed as UNKNOWN while the UI showed de1xl.** The name is not in
  the shot file — that is the uploaded JSON verbatim — it lives in the server's index.
  It now travels on the queue job, and the manual print path passes it from the card it
  was already displaying.
- **The plugin downloads 404'd on the tablet.** `npx cap copy` only moves `webDir`, and
  `plugin/plugin.tcl` lives at the repository root, so it never entered the APK — while
  the desktop packages, whose spec lists it, were fine. The build now bundles it under
  both names (`plugin.tcl` and `plugin.tcl.txt`; Android often refuses `.tcl` over
  Bluetooth and accepts `.txt`).
- **The test suite printed to real hardware.** `tests/web_test.html` really POSTs
  `/api/print`, and its comment — "no printer is attached, that is fine" — only holds on
  a machine with no printer configured. With a thermal printer as the system default,
  every test run pushed a full chart (about 94 KB) at it; a small-buffer thermal printer
  cannot absorb that, loses alignment and feeds paper continuously, and the only way to
  stop it was to cut the power. This is worth stating plainly: the runaway paper during
  this release's development was the test suite, not the app. The tests now start the
  server with `PTS_PRINT_DRYRUN=1`, which swaps in an adapter that validates the bitmap
  and succeeds **without touching a printer**.

### Web UI

- The GitHub button opens the file on GitHub rather than the releases page — a button
  that says "download" should not land you on a page you have to search.
- The plugin steps are no longer double-numbered: the `<ol>` numbers them, and the
  strings carried their own "1. 2. 3." on top.
- Step four shows this machine's actual address instead of the words "this machine's
  IP", and the address and the path are two steps, because they are two fields in the
  plugin.

### Verification

- On a Samsung SM-X210 running Android 16, printing to a **real Bluetooth thermal
  printer**: one upload produced exactly one receipt, the queue drained, and nothing
  printed again afterwards.
- The plugin downloads return 200 with byte-identical content to `plugin/plugin.tcl`.
- The full test suite passes, including four new de-duplication tests.

**Not verified**: the shots printed during testing came from a file rather than from a
real DE1, and nothing was printed through the desktop adapters — so CUPS, the Windows
spooler and the DE1's own upload path are still untested against paper.

## Downloads

| Platform | File | Notes |
|---|---|---|
| macOS (Apple Silicon) | `PrintTheShotNext-macos-arm64.zip` | unzip → allow once → double-click |
| macOS (Intel) | `PrintTheShotNext-macos-intel.zip` | same |
| Windows | `PrintTheShotNext-windows-x64.exe` | double-click |
| Linux | `PrintTheShotNext-linux` | `chmod +x` then run |
| Android | `PrintTheShotNext-android.apk` | install — it is its own server and shows its LAN address |

**Every build is a complete server, Android included.** Run any of them and open
<http://localhost:8000> — or, on Android, the app opens its own UI and also shows its LAN
address so the DE1 uploads straight to the tablet. There is no client mode and no
companion machine required.

### macOS: allow it once

```bash
xattr -dr com.apple.quarantine /Applications/PrintTheShot.app
```

or: try to open it once, then **System Settings → Privacy & Security → Open Anyway**.

> ⚠️ Not the same as *"the app is damaged"* — that means the code signature failed to
> validate and cannot be bypassed. `xattr` does not fix it either; you have a build from
> before the signing fix.

---

# 中文

让 Android 版真正能当后台打印节点用的一版。下面几乎所有改动都源于同一个事实:
**App 不在前台时,进程会被系统冻结**,而一旦冻结,整条链路是无声断掉的。

### 变更

- **后台常驻不再是可选项,服务类型也换了。** 此前全仓库没有任何地方调用
  `startForegroundService` —— 它只出现在插件定义和文档注释里,所以这个服务**永远
  启动不了**,App 一退到后台就被冻结。现在它随 App 自动启动,界面上给了关闭开关。
- **`dataSync` → `connectedDevice`。** 蓝牙打印机就是「外部设备」,这正是
  connectedDevice 的定义;更实际的是,Android 15 起 dataSync 有每日累计 6 小时的
  上限,到点会被系统停掉,而 connectedDevice 不在该限制的适用类型里。当初把这件事
  按「搬运数据」理解,等于给「常驻」判了死刑 —— 而常驻是这个服务唯一有意义的形式。

### 修复

- **退到后台就停止打印,队列没人取。** 队列泵是 WebView 里的 `setInterval`,而
  WebView 一旦隐藏,Chromium 会把它的定时器节流到大约一分钟一次,有时干脆不醒。
  现在由服务端在 shot 到达时主动唤醒前端 —— 走 `evaluateJavascript`,不是定时器,
  所以不受节流影响。渲染仍然只发生在前端。
- **同一张票会一直打下去。** 打印成功但回执没送到时,服务端不摘任务,下一轮又取到
  它 —— 实测踩过:打印机疯狂吐纸。现在「同一个文件只打一次」是这一端自己的不变量,
  **先记上再回执**,所以丢回执不会导致重打;另加每轮上限兜底。
- **同一个 shot 打了好几张。** DE1 侧的 `after_flow_complete` 可能重复触发,而客户端
  超时的上传其实已经落到服务端了,重试又存一份。每次上传的文件名都不同,按名字去重
  一个都拦不住。现在两端都按**内容**去重(SHA-256,30 秒窗口),前端每打一个任务就
  重新拉一次队列,让服务端摘掉的副本在打印之前就消失。
- **票上印的是 UNKNOWN,而界面显示 de1xl。** 机器名不在 shot 文件里(那份是上传的
  原始 JSON),它在服务端的索引里。现在它跟着队列任务走,手动打印那条路从卡片上带。
- **平板上插件下载全是 404。** `npx cap copy` 只搬 `webDir`,而 `plugin/plugin.tcl`
  在仓库根,于是从来没进过 APK —— 而桌面打包版(spec 里列了它)是好的。构建脚本
  现在把它打进 APK,并且放两个名字(`plugin.tcl` 和 `plugin.tcl.txt`;蓝牙传 .tcl
  常被安卓拒收,.txt 能过)。
- **测试套件会真的往打印机上打。** `tests/web_test.html` 里那句「真的发一次
  /api/print」,注释写着「没有打印机没关系」—— 这个前提只在**没配打印机的机器**上
  成立。当系统默认打印机是一台热敏机时,每跑一次测试都往它灌一张完整图表(约
  94 KB);缓冲小的热敏机顶不住,会错位并**持续走纸**,最后只能断电才停。

  这一点值得直说:**这一版开发过程中那些疯狂吐纸,是测试套件干的,不是 App。**
  测试现在用 `PTS_PRINT_DRYRUN=1` 起服务端,换成一个「照常校验、但不碰硬件」的
  适配器。

### Web 界面

- GitHub 按钮打开 GitHub 上的文件,而不是 releases 页面 —— 写着「下载」的按钮不该
  把人丢到一个还要自己找的页面上。
- 插件步骤不再重复编号:`<ol>` 自己会编号,而文案里又手写了一层「1. 2. 3.」。
- 第四步显示本机真实地址,而不是「本机IP」这几个字;地址和路径拆成两步,因为插件里
  它们本来就是两个字段。

### 验证

- Samsung SM-X210 / Android 16,**打到真实蓝牙热敏打印机**:一次上传只出一张票,
  队列清空后不再打印。
- 插件下载返回 200,内容与 `plugin/plugin.tcl` 逐字节一致。
- 全量测试通过,含四条新增的去重用例。

**未验证**:测试时打印的数据来自文件,不是真实 DE1 上传的;桌面端适配器(CUPS、
Windows 打印后台)也没有打过纸。

## 下载

| 平台 | 文件 | 说明 |
|---|---|---|
| macOS(Apple Silicon) | `PrintTheShotNext-macos-arm64.zip` | 解压 → 放行一次 → 双击 |
| macOS(Intel) | `PrintTheShotNext-macos-intel.zip` | 同上 |
| Windows | `PrintTheShotNext-windows-x64.exe` | 双击 |
| Linux | `PrintTheShotNext-linux` | `chmod +x` 后运行 |
| Android | `PrintTheShotNext-android.apk` | 装上即可 —— 它自己就是服务端,会显示本机地址 |

**每一个构建都是完整的服务端,Android 版也是。** 运行之后打开
<http://localhost:8000> —— Android 上应用会打开自己的界面,并且显示本机的局域网
地址,DE1 直接把数据传到平板即可。没有客户端模式,也不需要另一台机器配合。

### macOS:首次启动前放行一次

```bash
xattr -dr com.apple.quarantine /Applications/PrintTheShot.app
```

或者:先双击一次让它报错,然后到 **系统设置 → 隐私与安全性 → 点「仍要打开」**。

> ⚠️ 这和*「已损坏」*不是一回事 —— 后者是签名校验失败,绕不过去,`xattr` 也修不好;
> 那是签名修复之前的构建,换用当前版本。
