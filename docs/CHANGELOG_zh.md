# 变更日志

中文 | [English](CHANGELOG.md)

## 2.1-beta.4

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

## 2.1-beta.3

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
- 第四步显示本机真实地址,而不是「本机IP」这几个字,路径写在同一条里 —— 插件里是
  两个字段,但连着填。
- **步骤里补上「目标文件夹不存在要先建」。** 全新安装时
  `/de1plus/plugins/print_the_shot/` 并不存在,原来的说法会让人卡在第一步,不知道
  文件该往哪儿放。

### 验证

- Samsung SM-X210 / Android 16,**打到真实蓝牙热敏打印机**:一次上传只出一张票,
  队列清空后不再打印。
- 插件下载返回 200,内容与 `plugin/plugin.tcl` 逐字节一致。
- 全量测试通过,含四条新增的去重用例。

**未验证**:测试时打印的数据来自文件,不是真实 DE1 上传的;桌面端适配器(CUPS、
Windows 打印后台)也没有打过纸。

## 2.1-beta.2

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

## 2.1-beta.1

把绘制从服务端搬走、并把打印层做成可插拔的一版。这是结构性改动而非功能发布:
下面几乎每一条,都是「**浏览器负责画,服务端只做中转和调度**」这一个决定的结果。

### 架构

- **绘制搬进浏览器。** `web/render.js` 现在是项目里唯一的绘制实现,由旧的
  Pillow `ImageDraw` 代码直接翻译而来:每个几何常量都一一对应,数学部分没有改动。
  文字锚点按 Pillow 的语义(`la` / `rm` / `lm` / `mt`)在 Canvas 上重新实现,
  而不是用 Canvas 的默认锚点,所以字号、基线、对齐是**吻合**旧版输出,而不是
  「看着差不多」。
- **服务端不再产生任何图片。** PNG 输出、`shots_images/` 目录、`/images/*.png`
  和 `--render` 命令行参数全部移除。服务端现在只剩两件事:数据中转、打印调度。
- **`printers/` 是可插拔的包。** `BasePrinter` 定义接口,`load_printer()` 按
  `sys.platform` 挑适配器。上层只调接口,永远不需要知道底下是哪个平台。
- **打印由持有打印机的那一端触发。** 服务端把到达的 shot 挂进队列,浏览器(或
  Android WebView)取走、渲染、打印,再回执。以前是服务端自己渲染自己打印。

### 新增

- `web/render.js` —— `renderShotToCanvas()`、`canvasToBitmap()`、`bitmapToBase64()`
- `web/printer.js` —— 前端调度:桌面走 HTTP,Android 走原生蓝牙
- `web/bt.js` —— 蓝牙打印机设置页(权限、设备列表、连接状态、测试打印)
- `web/strings.js` —— 内置文案表(见下方「修复」里的第 2 条)
- `web/render.test.html` —— 独立绘制测试页,不需要服务端
- `printers/base.py` —— 接口定义,含 `bitmap_to_pbm()` 与 `bitmap_to_bmp1()`
- `printers/escpos.py` —— `GS v 0` 光栅位图,各平台共用
- `printers/mac_printer.py`、`linux_printer.py`、`windows_printer.py`、`android_printer.py`
- `/api/print` 改为接收 base64 编码的 1-bit 位图,不再接收文件名
- `/api/shot` —— 供前端绘制的完整 shot JSON
- `/api/printers`、`/api/print/config`(GET/POST)、`/api/print-queue`、`/api/print-queue/ack`
- `--list-printers` 与 `--print-mode` 命令行参数
- Web 界面新增**打印**卡片:平台、通道、打印机选择、模式、点数宽度
- 首次启动的服务端地址设置页(Android 专用)
- `scripts/build_android.sh` —— 一条命令从仓库构建 APK
- `tests/` —— 133 项测试,八个套件

### 变更

- 打印路径不再靠猜,而是协商:`driver` 与 `raw`(CUPS),`escpos` 与 `bmp`(Windows)。
- 打印失败会把适配器自己的原因透到界面上。没接打印机时,「没成功」是没用的信息。
- `web/index.html` 拆成 `index.html` + `app.js` + `style.css`。资源路径是相对路径,
  同时服务端也把 `web/` 挂在根路径上 —— 于是同一份 markup 在浏览器里和 APK 里
  (Capacitor 把资源放在根目录)都能正确解析。
- 切换语言改为按数据重画,不再为每种语言重渲染 PNG;图片缓存和后台重渲染线程
  一并移除。
- 服务更新现在会替换整个 `web/` 目录,而不只是 `index.html`。

### 修复

- 缩略图「空白」和「画了一块黑的」不再无法区分。浏览器测试只统计**不透明**的
  深色像素,所以一张从未绘制过的 canvas(全透明)不会再被算成 100% 有墨。
- `/api/shot` 拒绝目录穿越与不以 `.json` 结尾的文件名。
- 打印配置里的布尔值往返不再被破坏。
- **中文系统上认不出任何打印机。** `lpstat` 的输出是本地化的,而解析器在找英文
  单词 `printer`。改成解析行内的 ASCII 片段(CUPS 打印机名按规范都是 ASCII),
  中英两种写法都能解析。
- **APK 里整个界面白屏。** `{{LANG}}` 占位符由服务端替换,但 APK 里的文件是原样
  复制的,于是 `JSON.parse('{{LANG}}')` 在 app.js 第一行就抛异常。现在前端自带
  `web/strings.js` 兜底,服务端注入的表存在时覆盖在上面。
- **APK 里所有 API 请求打到 WebView 自己身上。** Web UI 打包进 APK 后,相对路径
  不再指向局域网服务端。新增服务端地址设置页,所有请求经 `apiUrl()` 解析。
- **字体没被打进 APK。** 字体已移入 `web/`。
- **`android.R.drawable.stat_sys_data_sync` 是隐藏资源**,编译报错;换成公开的。
- **XML 注释里的 `----`** 导致 `mergeDebugResources` 失败。
- **出图步骤画出整张白图**:填充色设成白色后忘了改回黑色。尺寸、字节数、ESC/POS
  指令头全部正常,只有内容全白。

### 移除

- Pillow,连同整个 `pip install` 步骤 —— **服务端现在只用标准库。**
- `render_chart()`、`generate_print_image()`、`print_image()`、`windows_print_bmp()`
  以及全部 Pillow 绘制辅助函数
- `smart_wrap_text()`(未被使用,且已被 `wrap_by_width()` 取代)
- `GET /images/*.png` 与 `python print_the_shot_server.py --render`

### Android

- Capacitor 外壳(`android/`),把同一份 Web UI 打包进 APK,通过原生插件走蓝牙
  ESC/POS 打印。
- **ESC/POS 协议在 JavaScript 里拼装,不在 Java 里。** 原生层只把字节写进 socket,
  别的什么都不做。同一份协议实现两遍 —— 桌面一份 JS、蓝牙一份 Java —— 迟早会走偏,
  而症状是「只有 Android 打出来不对」,在桌面上完全看不见。
- **刻意不依赖厂商 SDK。** 理由见 [android/README_zh.md](../android/README_zh.md)。

### 安全

- **`settings.json` 可以通过 HTTP 直接读走。** 服务端继承了
  `SimpleHTTPRequestHandler` 的目录提供行为,于是运行目录下的任何文件 ——
  包括 DeepSeek API key —— 同局域网任何人都能取走。而这个服务的设计前提**就是**
  局域网可达。这个问题从 Beta 继承而来。已改为显式白名单:未匹配的路径一律 404。

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
| `tests/apk_sim.html` | 12 | APK 那条代码路径(打包资源、服务端地址) |

共 133 项。服务端启动到可服务耗时 **0.165 秒**。

其中 CUPS 那组是最有力的:它建一台指向本地监听的虚拟打印机,通过适配器提交真实
任务,再把 CUPS 实际吐出的字节与送入的逐字节比对 —— `driver` 与 `raw` 两种模式
都做了。这是本环境里最接近真实打印机的一步。

**未验证:** 没有打印机,所以没有碰过任何硬件。字节布局有断言覆盖,纸面没有。

## 自 2.0-beta.2 继承

Beta 线的全部能力原样保留:DeepSeek 翻译、双语界面、内置 Noto Sans CJK SC、历史
持久化、日期筛选与分页、统计、插件分发(本地 / GitHub / TXT),以及自更新机制。
