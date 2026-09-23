# PrintTheShot Next v2.1-beta.5

---

# English

A quick follow-up to 2.1-beta.4: **on a fresh install the app crashed on launch**, so
2.1-beta.3 and 2.1-beta.4 are both broken for anyone installing them for the first time.
Upgrade if you installed either.

### Fixed

- **Crash on launch after a fresh install.** `connectedDevice`, the foreground service
  type introduced in 2.1-beta.3, requires not only its install-time permission but also
  at least one **granted** Bluetooth permission (`BLUETOOTH_CONNECT` and friends). Those
  are runtime permissions, and the service is started automatically when the app
  launches — so on the very first run nothing was granted yet and `startForeground`
  threw `SecurityException` every time.

  What made it fatal rather than annoying was a deliberate choice in the service: the
  `catch` around `startForeground` re-threw, on the reasoning that a foreground service
  which fails to start should not fail silently. The exception then escaped
  `onStartCommand` and took the whole app with it — before the user had seen a screen or
  had any opportunity to grant the permission. A dead end with no way out.

  The service now returns a failure instead of throwing, logs what is missing, stops
  itself, and the activity retries on resume — so the flow is: launch normally, grant
  the Bluetooth permission from the UI, and the keep-alive comes up by itself.

### Why this was not caught earlier

The tablet used for testing had `BLUETOOTH_CONNECT` granted long ago, and updating an
installed app does not revoke it — so the failure never appeared locally. It surfaced
only when installing a build signed with a different key, which reset the grants, and
that is exactly the state every new user starts in.

### Verification

- On a real fresh install (`adb uninstall`, then install, `BLUETOOTH_CONNECT` not
  granted): zero `FATAL EXCEPTION`, the server starts, and the log reads
  `BLUETOOTH_CONNECT not granted; keep-alive deferred until it is` → then, once the
  permission is granted, `keep-alive on` without any further action.

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

2.1-beta.4 的紧急跟进:**全新安装后 App 启动即闪退**,所以 2.1-beta.3 和
2.1-beta.4 对任何第一次安装的人都是坏的。装过其中任一版本请升级。

### 修复

- **全新安装后启动即闪退。** 2.1-beta.3 引入的 `connectedDevice` 前台服务类型,
  除了安装时权限,还要求**至少一个已授予的**蓝牙权限(`BLUETOOTH_CONNECT` 等)。
  那些是**运行时权限**,而常驻服务在 App 启动时自动拉起 —— 所以第一次运行时什么都
  还没授权,`startForeground` 每次都抛 `SecurityException`。

  让它从「烦人」变成「致命」的,是服务里一个刻意的选择:`startForeground` 外面那段
  `catch` 会**重新抛出去**,理由是「前台服务起不来不该静默失败」。异常于是从
  `onStartCommand` 冒出来,把整个 App 一起带走了 —— 而那时用户还没看到任何界面,
  也就没有任何机会去授权。一个走不出去的死循环。

  现在服务返回失败而不是抛出,把缺什么写进日志,自己停掉;Activity 在 onResume
  时重试 —— 于是流程变成:正常启动 → 在界面上授予蓝牙权限 → 常驻自己就起来了。

### 为什么之前没发现

测试用的平板早就授予过 `BLUETOOTH_CONNECT`,而更新已安装的 App 不会重置它,所以
本地从未复现。直到装了一个用**不同密钥签名**的包 —— 那会重置权限授予,而那正是
每一个新用户所处的状态。

### 验证

- 真机全新安装(`adb uninstall` 后重装,`BLUETOOTH_CONNECT` 未授予):
  `FATAL EXCEPTION` 数为 0,服务正常启动,日志依次是
  「蓝牙权限未授予,后台常驻暂不启动;授权后回到界面会自动开启」→
  然后在权限授予后自动出现「后台常驻已开启 / keep-alive on」,不需要任何额外操作。

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
