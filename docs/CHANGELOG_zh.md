# 变更日志

中文 | [English](CHANGELOG.md)

## 2.1-next.1

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
