# PrintTheShot Next v2.1-beta.2

---

# English

The release that made the Android app a server in its own right, reversing the client
shape shipped in 2.1-beta.1. The tablet now receives shots straight from the DE1,
renders them, and prints over Bluetooth — with no computer anywhere in the path.

### Changed

- **The Android app is now a complete server, not a client.** The previous build made
  the APK a client that connected to a desktop server; that was the wrong shape for
  this project, whose goal is a self-contained print node. Client mode and its
  server-address setup screen are gone entirely.
- **The routes mirror the desktop server**, so the same DE1 plugin configuration works
  against either end.

### Added

- `android/.../server/MiniHttpServer.java` — a hand-written HTTP server on
  `java.net.ServerSocket`, no dependencies. A library such as NanoHTTPD was rejected
  deliberately: the native sources are overlaid onto a Capacitor-generated project and
  this repository has no `build.gradle` in which to pin a dependency, so adding one
  would mean an implicit prerequisite that cannot be version-controlled.
- `android/.../server/AppServer.java` — routes: web UI, `/upload`, history, statistics,
  the pending-print queue, the printer list, `/api/print`.
- `android/.../server/ShotStore.java` — app-private storage (`getFilesDir`), so the app
  never requests a storage permission. Filenames carry a microsecond ID so two shots
  uploaded within the same second cannot collide.
- `android/.../server/DeviceInfo.java` — LAN address detection that enumerates
  interfaces and **excludes tunnel interfaces**, rather than the common "connect to
  8.8.8.8 and read back the source address" trick. That trick returns the VPN address
  whenever a VPN is up, and the user then types an address that cannot be reached — the
  desktop build hit exactly this.
- `android/.../server/ServerHolder.java` — one place for the service lifecycle, so an
  Activity recreation cannot start a second server. Reads the version from
  `strings.js`, so the native layer and the front end cannot disagree about it.
- The status card shows the tablet's LAN address, ready to paste into the DE1 plugin.

### Removed

- Client mode: `setServerBase()`, `needsServerConfig`, and the first-run server-address
  screen.
- **The "stop service" button does not appear on Android.** There is no terminal on a
  tablet, and stopping the service is equivalent to killing the app.

### Fixed

- **The packaged build no longer ships a literal `{{VERSION}}` in its page title.**
  Cosmetic on Android, since the WebView does not display the title, but wrong.
- **Template substitution is now limited to files under `web/`.** It previously applied
  to any `.html` / `.js` / `.css`, including the test pages under `tests/` — where it
  rewrote placeholder literals that existed as *assertion content*. One assertion
  holding `'{{VERSION}}'` had its literal replaced with the real version, so it silently
  became "the title must not contain 2.1-beta.1" and could never pass. The file looked
  perfectly normal throughout.

### Design notes

- **`/api/print` takes the complete ESC/POS byte stream, not a bitmap.** The protocol is
  assembled in exactly one place (`web/printer.js`, byte-for-byte with
  `printers/escpos.py`). Reimplementing it in Java would create a second place to drift,
  and the symptom would be "prints sent over the LAN differ from prints made from the
  app".
- **No template substitution in Java** — the front end resolves `{{VERSION}}` and
  `{{LANG}}` itself, verified on both the browser and the APK paths.
- **Desktop-only features return an empty result that explains itself, not a 404.** AI
  translation and online update are not implemented on Android; the front end calls
  those endpoints, and "this endpoint does not exist" and "this build does not have this
  feature" are different things.
- **The WebView still loads from APK assets** and talks to `http://localhost:8000` over
  CORS, rather than pointing Capacitor's `server.url` at the loopback — which keeps the
  working Bluetooth bridge untouched, and leaves the page renderable (reporting that it
  cannot connect) instead of blank when the service is not up.

### Verification

| Suite | Tests | Coverage |
|---|---|---|
| front-end JS syntax | 5 files | one bad paren = a blank UI |
| `tests/test_printers.py` | 18 | ESC/POS header, PBM/BMP encoding, validation |
| `tests/test_platform_dispatch.py` | 20 | platform detection, localised `lpstat`, Windows payload |
| `tests/test_server.py` | 22 | HTTP API, upload → history → queue, dispatch, exposure |
| `tests/test_cups_e2e.py` | 3 | **a real CUPS round trip**, bytes compared |
| `tests/web_test.html` | 36 | renderer maths, bitmap packing, the whole print API |
| `tests/ui_test.html` | 22 | the real UI in a real browser |
| `tests/apk_sim.html` | 11 | the APK code path (bundled assets, no client mode) |

132 tests in total, all passing.

**Verified on real hardware** — a Samsung SM-X210 running Android 16, on a real LAN:

- the tablet serves port 8000 to the LAN; `http://192.168.1.225:8000/` returned 200
  when requested from a Mac
- every static asset was retrievable, including the 16 MB font
- `POST /upload` succeeded — the shot was stored, parsed into a bean and a profile, and
  queued for printing
- the UI showed the tablet's own LAN address, the data card appeared, and the Canvas
  thumbnail rendered

**Not verified**: no printer and no DE1 were available, so the SPP handshake, the
512-byte chunking with a 20 ms gap, and the whole ESC/POS byte layout remain untested
against paper. The permission flows ran on Android 16 only, not on 12, 13 or 14.

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

让 Android 版成为独立服务端的一版,方向与 2.1-beta.1 相反。平板现在直接从 DE1
接收 shot、自己渲染、再走蓝牙打印 —— 整条链路上没有任何电脑。

### 变更

- **Android 版现在是完整的服务端,不再是客户端。** 之前把 APK 做成了「连桌面
  服务端」的客户端,对这个项目来说形态错了 —— 目标形态是自足的打印节点。客户端
  模式及其服务端地址设置页已全部移除。
- **路由与桌面版服务端对齐**,所以同一个 DE1 插件配置两边都能用。

### 新增

- `android/.../server/MiniHttpServer.java` —— 手写的 HTTP 服务,基于
  `java.net.ServerSocket`,零依赖。刻意放弃了 NanoHTTPD 之类的库:原生源码是以
  叠加方式合进 Capacitor 生成工程的,本仓库没有 `build.gradle` 可以用来锁定依赖,
  引入库就等于多一个无法版本控制的隐式前提。
- `android/.../server/AppServer.java` —— 路由:Web UI、`/upload`、历史、统计、
  待打印队列、打印机列表、`/api/print`。
- `android/.../server/ShotStore.java` —— 存应用私有目录(`getFilesDir`),应用
  从不申请存储权限。文件名带微秒 ID,同一秒内上传的两个 shot 不会撞名。
- `android/.../server/DeviceInfo.java` —— 取局域网地址时遍历网卡并**排除隧道
  接口**,而不是用常见的「连 8.8.8.8 看回程源地址」那招。后者开着 VPN 时会返回
  VPN 地址,用户照着填就连不上 —— 桌面端正是踩过这个坑。
- `android/.../server/ServerHolder.java` —— 服务生命周期收在一处,避免 Activity
  重建时起第二个服务。版本号从 `strings.js` 读,原生层和前端不可能对不上。
- 状态卡片显示平板的本机地址,方便直接填进 DE1 插件。

### 移除

- 客户端模式:`setServerBase()`、`needsServerConfig`,以及首次启动的服务端地址
  设置页。
- **Android 上不显示「停止服务」按钮。** 平板上没有终端,而停掉服务等于停掉整个
  应用。

### 修复

- **打包版的页面标题不再残留字面量 `{{VERSION}}`。** Android 的 WebView 不显示
  标题,所以是外观问题,但终究是错的。
- **模板替换范围收窄到 `web/` 下的文件。** 以前对任何 `.html` / `.js` / `.css` 都
  做替换,包括 `tests/` 下的测试页 —— 那会把**作为断言内容**出现的占位符字面量一并
  改写。有一条断言里的 `'{{VERSION}}'` 被换成了真实版本号,于是它悄悄变成了
  「标题里不能含 2.1-beta.1」,永远不可能通过。文件本身看起来完全正常。

### 设计取舍

- **`/api/print` 接收完整的 ESC/POS 字节流,不接受位图。** 协议拼装只有一份实现
  (`web/printer.js`,与 `printers/escpos.py` 逐字节对应)。在 Java 里再实现一遍
  等于开了第二个会走偏的地方,而症状会是「通过局域网打的和从应用里打的不一样」。
- **Java 端不做模板替换** —— `{{VERSION}}` 和 `{{LANG}}` 由前端自己兜底,已在
  浏览器与 APK 两条路径上验证过。
- **桌面端独有的功能返回说得清楚的空结果,而不是 404。** Android 上没有实现 AI
  翻译和在线更新;前端会调这些接口,而「接口不存在」和「这端没有这个功能」是两回事。
- **WebView 仍从 APK 资源加载**,API 走 `http://localhost:8000` + CORS,没有用
  Capacitor 的 `server.url` 直接指过来 —— 这样不动已经跑通的蓝牙桥,而且服务没起来
  时页面仍能显示(只是报连不上),不是白屏。

### 验证

| 测试 | 项数 | 覆盖 |
|---|---|---|
| 前端 JS 语法 | 5 个文件 | 一个括号写错 = 界面白屏 |
| `tests/test_printers.py` | 18 | ESC/POS 指令头、PBM/BMP 编码、校验 |
| `tests/test_platform_dispatch.py` | 20 | 平台探测、本地化 `lpstat`、Windows 载荷 |
| `tests/test_server.py` | 22 | HTTP 接口、上传→历史→队列、调度、暴露面 |
| `tests/test_cups_e2e.py` | 3 | **真实 CUPS 往返**,逐字节比对 |
| `tests/web_test.html` | 36 | 绘制数学、位图打包、整套打印 API |
| `tests/ui_test.html` | 22 | 真实浏览器里的真实界面 |
| `tests/apk_sim.html` | 11 | APK 那条代码路径(打包资源,已无客户端模式) |

共 132 项,全部通过。

**已在真机上验证** —— Samsung SM-X210,Android 16,接在真实局域网里:

- 平板对局域网提供 8000 端口;从 Mac 访问 `http://192.168.1.225:8000/` 返回 200
- 全部静态资源可取,包括 16 MB 的字体
- `POST /upload` 成功 —— 数据落盘,解析出豆子与方案,进入待打印队列
- 界面显示平板自己的局域网地址,数据卡片出现,Canvas 缩略图渲染成功

**未验证:** 手边没有打印机,也没有 DE1,所以 SPP 握手、512 字节分块 + 20 ms 间隔、
以及整套 ESC/POS 字节布局都没有在纸上验证过。权限流程只在 Android 16 上跑过,
没有在 12 / 13 / 14 上实测。

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
