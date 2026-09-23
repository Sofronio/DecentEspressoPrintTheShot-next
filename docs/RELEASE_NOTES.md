# PrintTheShot Next v2.1-beta.4

---

# English

A small release fixing two things that came from the same place: values in Capacitor's
generated project that nothing ever updated.

### Fixed

- **The release build could not be installed at all.** Capacitor's template has no
  signing config on the release build type, so `assembleRelease` produced an unsigned
  package and installation failed with
  `INSTALL_PARSE_FAILED_NO_CERTIFICATES`. That error never says "you did not sign it",
  which is a good part of why it took a while to place. The build now signs the release
  variant with the debug key — fine for sideloading, and a release build here is only a
  faster variant of the debug one. The key is generated if it is missing.
- **Every APK called itself 1.0.** The generated project hard-codes `versionCode 1` and
  `versionName "1.0"`, Capacitor's template values, so no build ever followed the app
  version and the system reported every release as the same "1.0". Both now come from
  `VERSION` in `print_the_shot_server.py` — the same source the UI and the release notes
  use. Odd as it sounds, this is the kind of breakage that survives for a long time: the
  APK still builds, still installs, and still works.

### CI

- The Android verification step now asserts that the APK's version matches the source.
  It already checked the package name, the app label and the bundled assets; a version
  that silently stops tracking the app was the one gap left.

### Verification

- The release APK verifies with `apksigner` (CN=Android Debug), installs, launches and
  does not crash.
- Both variants built from this source report `versionCode 20104` / `versionName
  2.1-beta.4`; the scheme is `major*10000 + minor*100 + prerelease number`, with a final
  release taking 99 within the same minor so it sorts after every prerelease.
- `debug → release → debug` builds cleanly in sequence. The first attempt at this patch
  was not idempotent: building release and then debug left a dangling
  `signingConfig` reference and failed with `unknown property 'debugInjected'`, pointing
  at `build.gradle` rather than at the script.

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

一个小版本,修的两件事其实同源:Capacitor 生成工程里那些**从来没人更新过**的模板值。

### 修复

- **release 包根本装不上。** Capacitor 模板里 `buildTypes.release` 没有签名配置,
  于是 `assembleRelease` 出来的是未签名包,安装直接失败:
  `INSTALL_PARSE_FAILED_NO_CERTIFICATES`。而这条报错**不会**告诉你「你没签名」——
  这也是它花了一阵子才被定位的原因之一。现在 release 变体用调试密钥签名:sideload
  测试够用,而且这个用途下 release 包本来也只是 debug 包的一个更快的变体。密钥不
  存在会自动生成。
- **所有包都自称 1.0。** 生成工程里写死 `versionCode 1` / `versionName "1.0"`
  (Capacitor 的模板值),任何一次构建都不跟 App 版本走,系统里所有版本都显示成同一个
  「1.0」。现在两者都取自 `print_the_shot_server.py` 的 `VERSION` —— 和界面、发布
  说明用的是同一个来源。说来奇怪,这类失效特别能活:包照样打得出来、装得上、也能用。

### CI

- Android 的校验步骤现在会断言 APK 的版本号与源码一致。它本来就查包名、应用名和
  打包资源,「版本号悄悄不再跟版本走」是剩下唯一的缺口。

### 验证

- release 包通过 `apksigner` 校验(CN=Android Debug),安装、启动、无崩溃。
- 用这份源码构建的两个变体都是 `versionCode 20104` / `versionName 2.1-beta.4`;
  编码规则是 `major*10000 + minor*100 + 预发布序号`,正式版取同一 minor 下的 99,
  好排在所有同名预发布之后。
- `debug → release → debug` 连续构建通过。这个补丁的第一版**不幂等**:构建完
  release 再构建 debug 会留下一个悬空的 `signingConfig` 引用,报
  `unknown property 'debugInjected'`,而报错指向 `build.gradle` 而不是脚本。

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
