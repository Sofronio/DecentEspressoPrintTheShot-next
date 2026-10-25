# PrintTheShot Next v1.0.0-beta.5

---

# English

The release candidate for 1.0.0. It contains the background Bluetooth printing fix,
the configurable print-queue timeout, and the Android UI fixes; 1.0.0 adds no code
changes beyond the version number.

### Fixed

- **Background Bluetooth printing.** A shot received while the app is backgrounded is
  now rendered synchronously and written to Bluetooth by the foreground service, instead
  of waiting for the app to come forward. The hidden WebView's async event loop is
  frozen, so the new path deliberately does not use it.
- **Stale print queue.** Pending jobs older than 3 minutes (configurable in Print
  settings) are dropped instead of printed in a burst when the app returns.
- **Android fixes in this build.** The print/bean-info switches now work and persist;
  receipts show the real machine name; saving PNG/JSON writes into Downloads; the UI
  language choice survives a reload; the large view has a working close button; Refresh
  reports completion.

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

1.0.0 的候选版本。包含后台蓝牙打印修复、可配置的打印队列超时,以及一批 Android
界面修复;1.0.0 相比这一版只改版本号,不再改代码。

### 修复

- **后台蓝牙打印。** 应用在后台时收到的 shot 现在会在隐藏页面里同步渲染,再由前台
  服务直接写蓝牙,不再等应用切回前台。隐藏 WebView 的异步事件循环会被冻结,所以新
  路径刻意不依赖它。
- **过期的打印队列。** 待打印任务超过 3 分钟(可在打印设置里改)会被丢弃,而不是在
  应用回来时补出一堆票。
- **这一版的 Android 修复。** 打印/豆子信息开关真正生效并持久化;票面显示真实机器
  名;保存 PNG/JSON 会写进「下载」;界面语言选择刷新后仍有效;大图有可用的关闭按钮;
  刷新会提示完成。

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
