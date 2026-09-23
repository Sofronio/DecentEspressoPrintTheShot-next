# PrintTheShot Next v2.1-beta.3

---

# English

The release that made the Android app usable as a background print node, and then had to
be reissued because the first attempt shipped broken.

**If you are reading this to decide whether to update: yes.** Two earlier builds under
this same version number were withdrawn — one crashed on launch for anyone installing it
fresh, and the other left the plugin download buttons doing nothing. This is the one that
was tested end to end on real hardware.

### Changed

- **The background service is no longer optional, and its type changed.** Nothing in the
  app ever called `startForegroundService` — it lived in the plugin and in a doc comment
  and nowhere else, so the service could never start and the app was frozen the moment it
  went to the background. It now starts with the app, and the UI has a switch to turn it
  off.
- **`dataSync` → `connectedDevice`.** A Bluetooth printer is an external device, which is
  what `connectedDevice` means; more to the point, from Android 15 `dataSync` carries a
  6-hour-per-24-hour cap and is stopped by the system when it expires, while
  `connectedDevice` is not among the types that limit applies to.

### Fixed

- **Printing stopped in the background.** The pump was a `setInterval` inside the
  WebView, and Chromium throttles a hidden page's timers to roughly once a minute. The
  server now pokes the front end when a shot arrives — an `evaluateJavascript` call, not
  a timer, so the throttling does not apply. Rendering still happens only in the front
  end.
- **The same receipt could print forever.** When a print succeeded but its
  acknowledgement did not arrive, the server kept the job and the next pass claimed it
  again; in practice the printer fed paper continuously. "Print each file once" is now
  this end's own invariant, recorded *before* the ack so a lost ack cannot reprint it.
- **The same shot printed several times.** De-duplication is now by **content**
  (SHA-256, 30 second window) on both the Android and the desktop server, and the front
  end re-fetches the queue per job so the copies the server drops disappear before they
  print.
- **The machine name printed as UNKNOWN while the UI showed de1xl.** The name is not in
  the shot file — that is the uploaded JSON verbatim — it lives in the server's index. It
  now travels on the queue job.
- **Crash on launch after a fresh install.** `connectedDevice` requires not only its
  install-time permission but also at least one **granted** Bluetooth permission, and
  those are runtime permissions — so on the very first run nothing was granted and
  `startForeground` threw every time. What made it fatal was that the service re-threw on
  purpose; the exception escaped `onStartCommand` and took the app with it, before the
  user had seen a screen or had any chance to grant anything. It now fails quietly, logs
  what is missing, and the activity retries on resume.
- **The plugin download buttons did nothing inside the APK.** A WebView does not save
  files and Capacitor sets no download handler; and the Android server was not sending
  `Content-Disposition`, which is why `.txt` was *displayed* rather than saved while
  `.tcl` raised a download event anyway. Handing the URL to the system browser turned out
  to be self-defeating — opening the browser backgrounds the app, and the file has to
  come from the app's own server, which is frozen by then. The buttons now use no
  network: the file ships inside the APK and the native side writes it into Downloads.
- **Upgrading required uninstalling, which wiped your data.** Every build environment
  signed with its own key, and each CI run generated a fresh one, so no build could
  replace another (`INSTALL_FAILED_UPDATE_INCOMPATIBLE`). There is now one committed key
  used by both variants. It is public and protects nothing; the build script says so, and
  says what to do if this ever needs a real release signature.

### Web UI

- The GitHub button opens the file on GitHub rather than the releases page.
- The plugin steps are no longer double-numbered, and now mention creating
  `/de1plus/plugins/print_the_shot/` when it is missing — that folder does not exist on a
  fresh install, so the old wording left the user stuck at step one.
- Step four shows this machine's actual address, with the path beside it.

### Verification

- On a Samsung SM-X210 running Android 16, printing to a **real Bluetooth thermal
  printer**: one upload produced exactly one receipt, the queue drained, and nothing
  printed again afterwards.
- A fresh install with no Bluetooth permission granted: zero crashes, and the keep-alive
  comes up on its own once the permission is granted.
- Both plugin buttons write 19,205 bytes — the size of `plugin/plugin.tcl` — into the
  device's Downloads folder.
- The debug and release APKs carry the same signing certificate.
- The full test suite passes, including four de-duplication tests.

**Not verified**: the shots printed during testing came from a file rather than from a
real DE1, and nothing was printed through the desktop adapters — CUPS, the Windows
spooler and the DE1's own upload path are still untested against paper.

### One more thing

During this release's development the printer fed paper continuously several times, and
it was traced to **the test suite**: `tests/web_test.html` really POSTs `/api/print`, and
its comment — "no printer is attached, that is fine" — only holds on a machine with no
printer configured. Every test run pushed a full chart at the default printer, and a
small-buffer thermal printer cannot absorb that. The tests now start the server with
`PTS_PRINT_DRYRUN=1`. If you run these tests, you get that fix too.

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

让 Android 版真正能当后台打印节点用的一版 —— 第一版发出去是坏的,所以重发了一次。

**如果你正在看这段来决定要不要更新:要。** 这个版本号下先后发过两个构建,都已撤回:
一个对全新安装的人启动即闪退,另一个的插件下载按钮点了没反应。这一版是在真机上
从头到尾验证过的那个。

### 变更

- **后台常驻不再是可选项,服务类型也换了。** 此前全仓库没有任何地方调用
  `startForegroundService` —— 它只出现在插件定义和文档注释里,所以这个服务**永远
  启动不了**,App 一退到后台就被冻结。现在它随 App 自动启动,界面上给了关闭开关。
- **`dataSync` → `connectedDevice`。** 蓝牙打印机就是「外部设备」,这正是
  connectedDevice 的定义;更实际的是,Android 15 起 dataSync 有每日累计 6 小时的上限,
  到点会被系统停掉,而 connectedDevice 不在该限制的适用类型里。

### 修复

- **退到后台就停止打印。** 队列泵是 WebView 里的 `setInterval`,而 WebView 一隐藏,
  Chromium 会把它的定时器节流到大约一分钟一次。现在由服务端在 shot 到达时主动唤醒
  前端 —— 走 `evaluateJavascript`,不是定时器,所以不受节流影响。
- **同一张票会一直打下去。** 打印成功但回执没送到时,服务端不摘任务,下一轮又取到
  它 —— 实测把打印机打到持续走纸。「同一个文件只打一次」改成这一端自己的不变量,
  **先记上再回执**,所以丢回执不会导致重打。
- **同一个 shot 打了好几张。** 两端都按**内容**去重(SHA-256,30 秒窗口),前端每打
  一个任务就重新拉一次队列,让服务端摘掉的副本在打印之前就消失。
- **票上印的是 UNKNOWN,而界面显示 de1xl。** 机器名不在 shot 文件里(那份是上传的
  原始 JSON),它在服务端的索引里。现在跟着队列任务走。
- **全新安装后启动即闪退。** `connectedDevice` 除了安装时权限,还要求**至少一个已
  授予的**蓝牙权限,而那些是运行时权限 —— 第一次运行时一个都没有,`startForeground`
  每次都抛异常。让它变成致命的是服务里那个**故意 rethrow**:异常从 `onStartCommand`
  冒出去,把整个 App 带走了,而用户还没看到任何界面、也没机会授权。现在它安静地失败、
  把缺什么写进日志,Activity 在 onResume 时重试。
- **插件下载按钮在 APK 里点了没反应。** WebView 不会保存文件、Capacitor 也没设下载
  处理;而 Android 服务端没发 `Content-Disposition` —— 所以 `.txt` 被**显示**出来而不是
  保存,`.tcl` 反倒因为渲染不了触发了下载事件。把 URL 交给系统浏览器是**自相矛盾**的:
  浏览器一起来 App 就退到后台,而文件要从 App 自己的服务端取,那时它已经被冻住了。
  现在这两个按钮**完全不联网**:文件在 APK 里,由原生写进「下载」目录。
- **升级必须先卸载,而卸载会清数据。** 每个构建环境用自己的密钥,CI 每次跑还现生成
  一把新的,于是任何两个包都覆盖不了(`INSTALL_FAILED_UPDATE_INCOMPATIBLE`)。现在
  仓库里放了一把固定密钥、两个变体共用。它是**公开的**、保护不了任何东西 —— 构建
  脚本里写明了,也写了哪天需要真正的发布签名该怎么做。

### Web 界面

- GitHub 按钮打开 GitHub 上的文件,而不是 releases 页面。
- 插件步骤不再重复编号,并且补上「目标文件夹不存在要先建」—— 全新安装时
  `/de1plus/plugins/print_the_shot/` 并不存在,原来的说法会让人卡在第一步。
- 第四步显示本机真实地址,路径写在同一条里。

### 验证

- Samsung SM-X210 / Android 16,**打到真实蓝牙热敏打印机**:一次上传只出一张票,
  队列清空后不再打印。
- 全新安装且未授予蓝牙权限:零崩溃;权限授予后常驻自己起来。
- 两个插件按钮都把文件写进设备的「下载」目录,19,205 字节 —— 正是
  `plugin/plugin.tcl` 的尺寸。
- debug 与 release 两个包签的是同一张证书。
- 全量测试通过,含四条去重用例。

**未验证**:测试时打印的数据来自文件,不是真实 DE1 上传的;桌面端适配器(CUPS、
Windows 打印后台)也没有打过纸。

### 另外一件事

这一版开发过程中,打印机几次疯狂吐纸,最后查到是**测试套件**干的:`tests/web_test.html`
里那句「真的发一次 `/api/print`」,注释写着「没有打印机没关系」—— 而那个前提只在**没配
打印机的机器**上成立。每跑一次测试都往默认打印机灌一张完整图表,缓冲小的热敏机顶不住。
现在测试用 `PTS_PRINT_DRYRUN=1` 起服务端。跑这个项目测试的人,也一并拿到这个修复。

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
