# PrintTheShot Next v1.0.0

---

# English

The first stable release of the 1.0 line. The headline is **auto-printing while the
Android app is in the background** — the way this app is meant to be used: Decaid in
front, the tablet waiting behind it.

### Fixed

- **Background Bluetooth printing now works.** Shots received while the app was
  backgrounded used to sit in the queue until the app came forward. Bluetooth was not
  the problem: Chromium freezes the hidden WebView's event loop, so the printing code
  stalled on its first network request and never sent anything. The server now renders
  the receipt synchronously inside the WebView, receives the ESC/POS bytes, and writes
  them to Bluetooth from its foreground service. Verified on the tablet with a shot
  uploaded while the app was in the background.
- **Old queue entries no longer print in a burst when the app comes back.** Pending
  jobs older than **3 minutes** are dropped instead of printed as a backlog. The timeout
  is configurable in Print settings; the shot history is untouched.
- **The two switches on the status card now work on Android.** Turning printing off
  stops auto-printing but still stores new shots; turning bean info off removes that
  column from the chart. Both persist across restarts.
- **Receipts show the correct machine name.** The large-view and thumbnail print paths
  read the raw shot JSON, which has no machine name; the server now fills it in, so a
  receipt no longer says `UNKNOWN` while the UI shows `de1xl`.
- **Saving a PNG or JSON from the large view works in the APK.** A WebView ignores
  `<a download>`; the native plugin now writes the bytes into Downloads. The large view
  also has a real close button.
- **The UI language choice survives a reload in the APK**, and the server's injected
  language table is actually used. A placeholder-substitution bug had made the language
  switch a no-op in both the browser and the APK.
- **Refresh now reports that it finished**, instead of looking like it did nothing.

### Changed

- `1.0.0` is the first stable release; it sorts after every `1.0.0-beta.N`, so beta
  users can update in place.

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

1.0 线的第一个稳定版。这一版最重要的是 **Android 应用在后台时,自动打印现在真的能
工作** —— 这正是它被设计出来的使用形态:Decaid 在前台,平板在后台待命。

### 修复

- **后台蓝牙打印。** 应用在后台时收到的 shot 以前只入队,直到切回前台才出票。
  原因不在蓝牙:Chromium 会冻结隐藏 WebView 的事件循环,前端卡在第一个网络请求,
  根本到不了打印调用。现在服务端在 WebView 里做一次**同步**渲染,拿回 ESC/POS
  字节后由前台服务直接写蓝牙。已在平板上验证:后台收到的 shot 不需要把应用切到
  前台就能出票。
- **重新打开应用时,过期的打印队列不会再补出一堆积压的票。** 待打印任务超过
  **3 分钟**会被丢弃而不是补打,超时时间可在打印设置里改;shot 历史本身不受影响。
- **状态卡上的两个开关在 Android 上真正生效。** 关闭打印后不再自动出票,但数据
  照收照存;关闭豆子信息后图上那一栏消失。两个选择都会持久化,重启后仍有效。
- **票面上的机器名正确了。** 大图和缩略图打印走的是 shot JSON,里面没有机器名;
  服务端现在会补上,不会再出现界面写着 de1xl、票上却是 `UNKNOWN`。
- **APK 里大图的「存 PNG」「存 JSON」现在真的会保存文件。** WebView 不处理
  `<a download>`,现在由原生插件把字节写进「下载」目录;大图也补了一个明确的
  关闭按钮。
- **APK 里的界面语言选择在刷新后仍然有效**,服务端注入的语言表也真正被用上了。
  之前那个占位符替换 bug 让切换按钮在浏览器和 APK 里都形同虚设。
- **「刷新」按钮现在会告诉你它刷完了**,而不是看起来没反应。

### 变更

- `1.0.0` 是第一个稳定版,版本排序在每一个 `1.0.0-beta.N` 之后;beta 用户可以直接
  覆盖升级。

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
