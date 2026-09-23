# PrintTheShot Next v1.0.0-beta.4

---

# English

The release that makes your shot data yours to keep — export it, restore it — and makes
"check for updates" actually check. It also renumbers the version.

**If you are on 2.1-beta.anything: update by hand, this once.** The scheme changed to
`1.0.0-beta.N`, so your 2.1-beta.3 is now 1.0.0-beta.3. A build running the old numbering
cannot see the new one: it compares 2.1 against 1.0, concludes it is ahead, and reports
"already up to date" forever. One manual update fixes this permanently — the new build
understands both numberings.

### Changed

- **Backups can now be exported, and put back.** "Backup" used to run once,
  automatically, inside the service-update flow: invisible, unreachable, and useful in
  exactly one scenario. What actually loses data is **uninstalling** (which wipes the
  app's private directory) and **switching devices** — and both happen outside the app,
  so it never got the chance to back itself up first. There is now a Backup & restore
  card: export downloads a zip of every shot record, and import restores it. Importing
  **overwrites records with the same filename** — it is a restore, not a merge — and it
  neither prints nor queues anything, because restoring history is not new data.
- **On Android, export and import go through native code.** A WebView cannot save a
  file, and handing the URL to the system browser is self-defeating: the browser takes
  the foreground, this app goes to the background, and the file has to come from this
  app's own server — so the side providing it is frozen exactly when it is asked. Export
  now writes straight into Downloads through the native plugin; import opens the system
  file picker and posts the bytes to the local server itself.
- **"Check for updates" now really checks, on every platform.** Android used to answer
  "this build updates by installing a new APK — online update is not available". True,
  but it told the user nothing they wanted to know. The tablet now queries GitHub for the
  latest release and compares it with the version it is running.
- **Two channels: stable and beta.** Checking stable only considers final releases;
  checking beta includes pre-releases and takes whichever has the higher version. Two
  buttons rather than one auto-detecting one, so the choice is the user's.
- **Builds that cannot update in place now say where to get the new version.** The
  source-mode desktop updates itself; the packaged desktop and the APK open that
  release's page. `/api/status` and `/api/update/check` return a single word—
  `update_via`, one of `self`, `installer`, `apk`—and the UI follows it. All three share
  one code path.
- **The version scheme is `1.0.0-beta.N`** (it was `2.1-beta.N`), and the old releases
  were renamed on GitHub to match. The prerelease segment is what orders them: the final
  `1.0.0` sorts after every beta of itself.
- **`VERSION_CODE` is no longer derived from the version.** It used to be: `2.1-beta.3`
  produced 20103. Under the new numbering that formula yields a value **lower than every
  installed APK**, so Android would refuse the install as a downgrade and the user would
  have to uninstall — which is exactly what wipes their shot data. It is now an
  independent number that only ever goes up.

### Fixed

- **Importing a file that is not a backup reported success.** Feeding the Android server
  a non-zip returned `success: true, imported: 0`. Java's `ZipInputStream` does not throw
  on garbage — it simply yields no entries, which is indistinguishable from an empty
  archive. The archive is now opened as a whole, so "this is not a backup" is an error,
  and an archive carrying an illegal entry path is refused with a message that says so
  rather than claiming it is not a zip.
- **The test runner had been reporting a false green.** `tests/run_all.sh` tested the
  exit code of `tail` at the end of a pipe rather than the suite's own, so four of its
  five suites printed "✅ passed" however badly they failed. Two failures had been sitting
  in the repository unnoticed. It now propagates the real exit code.
- **Update checking no longer follows the `main` branch.** It compares against the latest
  release instead, so "there is an update" always means "there is a released, tested
  version" rather than "someone pushed a version bump".

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

这一版让你的 shot 数据真正属于你自己 —— 能导出、也能导回来 —— 并且让「检查更新」真的
去检查。版本编号也在这一版换掉了。

**如果你在用 2.1-beta.any:这次请手动更新一次。** 编号方案换成了 `1.0.0-beta.N`,
所以你的 2.1-beta.3 现在叫 1.0.0-beta.3。跑着旧编号的版本**看不到新编号**:它会拿 2.1
和 1.0 比,得出「我更新」,于是永远显示「已是最新」。手动更新一次之后就不会再有问题
—— 新版本认得两套编号。

### 变更

- **备份现在能导出,也能导回来。** 原来的「备份」只在服务更新流程里自动跑一次:用户
  看不到、也调不到,而它能救的场景只有一个。真正会丢数据的是**卸载重装**(会清掉
  App 私有目录)和**换设备** —— 那两件事都发生在 App 之外,它根本没有机会先备份自己。
  现在界面多了「备份与恢复」卡片:导出会把全部 shot 记录打成一个 zip 下载,导入则把它
  恢复回来。导入是**按文件名覆盖**——是恢复,不是合并 —— 而且不会触发打印、也不入队,
  因为恢复历史不是新数据。
- **Android 上的导出与导入走原生。** WebView 存不了文件;把地址交给系统浏览器则自相
  矛盾:浏览器一起来本 App 就退到后台,而文件要从本 App 自己的服务端取 —— 提供文件的
  那一端在被取的那一刻正好被系统冻住。现在导出由原生插件直接写进「下载」目录,导入弹
  系统文件选择器、由原生把字节发给本机服务端。
- **「检查更新」现在真的检查,而且每个平台都是。** Android 端从前回的是「本版通过安装
  新 APK 更新,不支持在线更新」—— 诚实,但没回答用户想知道的事。平板现在会去 GitHub
  查最新的 release,和自己在跑的版本比。
- **两个频道:稳定版与 Beta 版。** 查稳定版只在正式版里挑;查 Beta 版把预发布一起算上,
  取版本号更高的那个。做成两个按钮而不是一个自动判断的,是为了让这个选择落在用户手里。
- **不能原地更新的版本,现在会告诉你去哪儿拿新版。** 源码模式桌面端就地更新;打包版
  桌面端和 APK 会打开那个 release 的页面。`/api/status` 和 `/api/update/check` 返回一个
  词 —— `update_via`,取 `self` / `installer` / `apk` —— 界面照着它走,三种情况共用同
  一条代码路径。
- **版本编号换成 `1.0.0-beta.N`**(原来是 `2.1-beta.N`),旧的 release 也已在 GitHub 上
  改名对齐。排序靠的是预发布段:正式版 `1.0.0` 排在它自己的所有 beta 之后。
- **`VERSION_CODE` 不再由版本号推导。** 从前是推导的:`2.1-beta.3` 得到 20103。换编号
  之后同一条公式算出来的值**低于所有已装 APK**,Android 会以「降级」为由拒绝安装,用户
  只能卸载重装 —— 而卸载正是会清光他 shot 数据的那个动作。现在它是一个只增不减的独立数字。

### 修复

- **导入一个根本不是备份的文件,会被报成成功。** 给 Android 服务端喂非 zip 数据,它回
  `success: true, imported: 0`。Java 的 `ZipInputStream` 对垃圾数据不抛异常,只是一个
  条目都读不出来 —— 那和「包是空的」长得一模一样。现在整包打开,所以「这不是备份」是
  一个错误;而含非法条目路径的包会被拒绝,并且消息如实说明原因,而不是谎称「不是 zip」。
- **测试运行器一直在报假绿。** `tests/run_all.sh` 的判定挂在管道末尾,测的是 `tail` 的
  退出码而不是被测套件的,于是五组里有四组不管怎么失败都打印「✅ 通过」。仓库里因此躺着
  两个没人发现的失败用例。现在它传递真实的退出码。
- **「检查更新」不再跟着 `main` 分支走。** 改成对比最新的 release,于是「有更新」永远
  等于「有一个已发布、测过的版本」,而不是「有人推了一次版本号」。

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
