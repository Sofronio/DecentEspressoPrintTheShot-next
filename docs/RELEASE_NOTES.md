# PrintTheShot Next v2.1-beta.2

> Android 版成为独立打印节点 / **the Android app is now a standalone print node**

---

# English

## What changed since v2.1-beta.1

**The Android app is now a complete server, not a client.** This is the headline change,
and it reverses what v2.1-beta.1 shipped. The previous build made the APK a client that
connected to a desktop server; that was the wrong shape for this project. The APK is now
a print node in its own right — the DE1 uploads shot JSON straight to the tablet, the
tablet renders the chart and prints over Bluetooth, and **no computer is involved at any
point**. Client mode and its server-address settings screen are gone.

**A hand-written HTTP server in Java.** `MiniHttpServer.java` is built on
`java.net.ServerSocket` with no dependencies at all. A library such as NanoHTTPD was
considered and rejected: the native sources are *overlaid* onto a Capacitor-generated
project, and this repository has no `build.gradle` in which to pin a dependency — adding
one would mean an implicit prerequisite that cannot be version-controlled.

**The routes mirror the desktop server.** `AppServer.java` serves the web UI, `/upload`,
history, statistics, the pending-print queue, the printer list and `/api/print`. Because
the API matches the Python server's, **the same DE1 plugin configuration works against
either end**.

**Storage needs no permission.** `ShotStore.java` writes into the app-private directory
(`getFilesDir`), so the app never asks for storage access. Filenames carry a microsecond
ID, so two shots uploaded within the same second cannot collide.

**LAN address detection avoids the VPN trap.** `DeviceInfo.java` enumerates network
interfaces and deliberately *excludes* tunnel interfaces, rather than using the common
"connect to 8.8.8.8 and read back the source address" trick. That trick returns the VPN
address whenever a VPN is up, and the user then types an address that cannot be reached.
The desktop build hit exactly this, which is why it is handled explicitly here.

**One server, and only one.** `ServerHolder.java` keeps the service lifecycle in a single
place so that an Activity recreation cannot start a second server. It reads the version
number from `strings.js`, so the native layer and the front end cannot disagree about it.

**The status card shows the tablet's LAN address**, ready to paste into the DE1 plugin.

### Design notes

- **`/api/print` takes the complete ESC/POS byte stream, not a bitmap.** The protocol is
  assembled in exactly one place (`web/printer.js`, byte-for-byte with
  `printers/escpos.py`). Reimplementing it in Java would create a second place to drift,
  and the symptom would be "prints sent over the LAN come out different from prints made
  from the app" — the worst kind of bug to track down.
- **No template substitution in Java.** The `{{VERSION}}` placeholder in `index.html`
  and `{{LANG}}` in `app.js` are resolved by the front end itself, verified on both the
  browser and the APK paths.
- **Desktop-only features return an empty result that explains itself, not a 404.** AI
  translation and online update are not implemented on Android. The front end calls those
  endpoints, and "this endpoint does not exist" and "this build does not have this
  feature" are two different things.
- **No "stop service" button on Android.** There is no terminal on a tablet, and stopping
  the service is equivalent to killing the app — that is not a button worth offering.
- **The WebView still loads from APK assets and talks to `http://localhost:8000` over
  CORS**, rather than pointing Capacitor's `server.url` at the loopback. That keeps the
  already-working Bluetooth bridge untouched, and when the service is not up the page
  still renders (reporting that it cannot connect) instead of going blank.

## Fixed

- **The packaged build no longer ships a literal `{{VERSION}}` in its page title.**
  Cosmetic on Android, since the WebView does not display the title, but wrong.
- **Template substitution is now limited to files under `web/`.** It previously applied
  to any `.html` / `.js` / `.css`, including the test pages under `tests/` — where it
  rewrote placeholder literals that existed as *assertion content*. One assertion holding
  `'{{VERSION}}'` had its literal replaced with the real version, so the assertion
  silently became "the title must not contain 2.1-beta.1" and could never pass. The file
  looked perfectly normal throughout; that is the kind of failure that costs an afternoon.
- The `apk_sim` assertions now build the placeholder at runtime instead of relying on a
  literal.

## Verified on real hardware

A Samsung SM-X210 running Android 16, on a real LAN:

- the tablet serves port 8000 to the LAN — `http://192.168.1.225:8000/` returned 200
  when requested from a Mac
- every static asset was retrievable, including the 16 MB font
- `POST /upload` succeeded: the shot was stored, parsed into a bean and a profile, and
  queued for printing
- the tablet UI showed its own LAN address, the data card appeared, and the Canvas
  thumbnail rendered
- the full automated suite passes: 7 groups, 132 assertions, CUPS round trip included

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

## Known limitations

- **Still nothing has been verified against a real printer.** No printer and no DE1 were
  available, so the SPP handshake, the 512-byte chunking with a 20 ms gap, and the whole
  ESC/POS byte layout remain untested against paper. The byte layouts *are* covered by
  automated tests; **paper is not**.
- **The APK is a debug build** — signed with the debug keystore, not release-signed.
  Fine for testing, not for distribution through a store.
- **Intel macOS was tested under Rosetta only**, not on real Intel hardware.
- The macOS builds are **not notarized** (that needs a paid Apple Developer account), so
  the first launch needs a one-time allow — see below.
- **The Android native sources are an overlay applied by hand** onto the
  Capacitor-generated project. There is no script keeping `app/src/main/` in sync;
  re-copy after editing.

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

## 相比 v2.1-beta.1 改了什么

**Android 版现在是完整的服务端,不再是客户端。** 这是本次的主要改动,方向与
v2.1-beta.1 相反。之前把 APK 做成了「连桌面服务端」的客户端,对这个项目来说形态
错了。现在 APK 本身就是一个打印节点 —— DE1 把 shot JSON 直传到平板,平板渲染图表
并通过蓝牙打印,**全程不需要任何电脑参与**。客户端模式及其服务端地址设置页已全部
移除。

**手写的 Java HTTP 服务。** `MiniHttpServer.java` 基于 `java.net.ServerSocket`,
零依赖。考虑过 NanoHTTPD 之类的库,但放弃了:原生源码是以**叠加**方式合进 Capacitor
生成工程的,本仓库里没有 `build.gradle` 可以用来锁定依赖 —— 引入库就等于多一个
无法版本控制的隐式前提。

**路由与桌面版服务端对齐。** `AppServer.java` 提供 Web UI、`/upload`、历史、统计、
待打印队列、打印机列表和 `/api/print`。接口与 Python 服务端一致,所以**同一个 DE1
插件配置两边都能用**。

**存储不需要任何权限。** `ShotStore.java` 写进应用私有目录(`getFilesDir`),应用
从不申请存储权限。文件名带微秒 ID,同一秒内上传的两个 shot 不会撞名。

**局域网地址的取法规避了 VPN 陷阱。** `DeviceInfo.java` 遍历网卡并**刻意排除隧道
接口**,而不是用常见的「连 8.8.8.8 看回程源地址」那招。后者在开着 VPN 时会返回
VPN 地址,用户照着填就连不上 —— 桌面端正是踩过这个坑,所以这里显式处理。

**服务只有一个,不会起两个。** `ServerHolder.java` 把服务生命周期收在一处,避免
Activity 重建时起第二个服务。版本号从 `strings.js` 读,原生层和前端不可能对不上。

**界面状态卡片显示平板的本机地址**,方便直接填进 DE1 插件。

### 设计取舍

- **`/api/print` 接收完整的 ESC/POS 字节流,不接受位图。** 协议拼装只有一份实现
  (`web/printer.js`,与 `printers/escpos.py` 逐字节对应)。在 Java 里再实现一遍
  等于开了第二个会走偏的地方,而症状会是「通过局域网打的和从应用里打的不一样」——
  最难查的那类问题。
- **Java 端不做模板替换。** `index.html` 的 `{{VERSION}}` 和 `app.js` 的 `{{LANG}}`
  由前端自己兜底,已在浏览器与 APK 两条路径上验证过。
- **桌面端独有的功能返回说得清楚的空结果,而不是 404。** Android 上没有实现 AI
  翻译和在线更新。前端会调这些接口,而「接口不存在」和「这端没有这个功能」是两回事。
- **Android 上不显示「停止服务」按钮。** 平板上没有终端,而停掉服务等于停掉整个
  应用 —— 不该给一个一键自杀的入口。
- **WebView 仍从 APK 资源加载,API 走 `http://localhost:8000` + CORS**,没有用
  Capacitor 的 `server.url` 直接指过来。这样不动已经跑通的蓝牙桥;而且服务没起来时
  页面仍能显示(只是报连不上),不是白屏。

## 修复

- **打包版的页面标题不再残留字面量 `{{VERSION}}`。** Android 的 WebView 不显示
  标题,所以是外观问题,但终究是错的。
- **模板替换范围收窄到 `web/` 下的文件。** 以前对任何 `.html` / `.js` / `.css` 都做
  替换,包括 `tests/` 下的测试页 —— 那会把**作为断言内容**出现的占位符字面量一并
  改写。有一条断言里的 `'{{VERSION}}'` 被换成了真实版本号,于是断言悄悄变成了
  「标题里不能含 2.1-beta.1」,永远不可能通过。文件本身看起来完全正常,极难排查。
- `apk_sim` 的断言改为在运行时拼装占位符,不再依赖字面量。

## 真机验证

Samsung SM-X210,Android 16,接在真实局域网里:

- 平板对局域网提供 8000 端口 —— 从 Mac 访问 `http://192.168.1.225:8000/` 返回 200
- 全部静态资源可取,包括 16 MB 的字体
- `POST /upload` 成功:数据落盘,解析出豆子与方案,进入待打印队列
- 平板界面显示本机地址,数据卡片出现,Canvas 缩略图渲染成功
- 全量自动化测试通过:7 组、132 项断言,含真实 CUPS 往返

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

## 已知限制

- **仍未与真实打印机联调。** 手边没有打印机,也没有 DE1,所以 SPP 握手、512 字节
  分块 + 20 ms 间隔、以及整套 ESC/POS 字节布局都没有在纸上验证过。字节布局**有**
  自动化测试覆盖,**纸面没有**。
- **APK 是 debug 构建** —— 用 debug keystore 签名,不是发布签名。用于测试没问题,
  不适合上架分发。
- **Intel 版 macOS 只在 Rosetta 下测过**,没有真实 Intel 机器。
- macOS 版**未做公证**(需要付费的 Apple 开发者账号),首次启动需要放行一次 —— 见下。
- **Android 原生源码是手工叠加**到 Capacitor 生成工程上的,没有脚本保证
  `app/src/main/` 同步;改完要重新拷。

### macOS:首次启动前放行一次

```bash
xattr -dr com.apple.quarantine /Applications/PrintTheShot.app
```

或者:先双击一次让它报错,然后到 **系统设置 → 隐私与安全性 → 点「仍要打开」**。

> ⚠️ 这和*「已损坏」*不是一回事 —— 后者是签名校验失败,绕不过去,`xattr` 也修不好;
> 那是签名修复之前的构建,换用当前版本。
