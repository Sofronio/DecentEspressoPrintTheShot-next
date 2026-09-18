# 进度报告

中文 | [English](PROGRESS.md)

**截至 2026-09-18。** `convert.md` 里描述的迁移,macOS 与 Android 两端在软件层面都已完成。**没有经过任何真机验证** —— 手边没有打印机。

---

## 要求做的,和实际交付的

| 任务(来自 `convert.md`) | 状态 |
|---|---|
| 1. Web UI 端 Canvas 绘制 | **完成** —— `web/render.js`,项目里唯一的绘制实现 |
| 2. 统一打印接口 | **完成** —— `printers/base.py` + `POST /api/print` |
| 3. Mac 打印适配 | **完成** —— `printers/mac_printer.py`,已通过真实 CUPS 验证 |
| 4. Android 打印适配 | **完成** —— Capacitor 插件 + `printers/android_printer.py` 桥接 |
| 5. 服务端重构 | **完成** —— Pillow 与全部渲染逻辑已移除 |
| 6. Web UI 重构 | **完成** —— 不再依赖 PNG,全 Canvas |

清单之外还做了:Linux 与 Windows 适配器、首次启动的服务端地址设置页、蓝牙设置页,以及一个 APK。

---

## 验收标准

### macOS

| 标准 | 状态 |
|---|---|
| 打开 Web UI 能看到历史 shot 的 Canvas 缩略图 | **达成** —— 真实浏览器中验证 |
| 点击缩略图能看大图 | **达成** —— 已验证 |
| 打印能到配置的 80mm 打印机 | **字节层面达成** —— 走了真实 CUPS,见下 |
| 服务端不再生成 PNG | **达成** —— `/images/*` 返回 404,图片目录已不存在 |

打印链路在没有硬件的情况下做到了端到端验证:建一台指向本地监听的 CUPS 虚拟打印机,通过适配器提交真实任务,再把 CUPS 实际吐出的字节与送入的逐字节比对。两种模式都通过:

- `driver` 模式:CUPS 吐出合法 PBM,数据逐字节一致
- `raw` 模式:CUPS 吐出 `ESC @` + `GS v 0` + 数据 + 切纸,逐字节一致

这是没有打印机时能做到的最强验证。它证明了 CUPS 调用方式、参数、文件格式、字节布局全都正确。它**不能**证明打印机收到之后印得对。

### Android

| 标准 | 状态 |
|---|---|
| Web UI 在 Android WebView 里正常运行 | **达成** —— 已打包进 APK,并在包内验证 |
| 扫描并连接蓝牙打印机 | **已实现,未实际跑过** —— 没有硬件 |
| 蓝牙 ESC/POS 打印曲线图 | **已实现,未实际跑过** —— 没有硬件 |
| 前台服务保活 | **已实现,未实际跑过** —— 需要 Doze 环境 |

### 通用

| 标准 | 状态 |
|---|---|
| 服务端启动开销明显下降 | **达成** —— 0.165 秒,且不 import 任何标准库之外的东西 |
| Mac 与 Android 渲染结果一致 | **由结构保证** —— 同一份绘制代码、同一份字体,两边都打包 |

---

## 验证情况

133 项自动化测试,全部通过:

| 测试 | 项数 | 需要 |
|---|---|---|
| 前端 JS 语法 | 5 个文件 | `node` |
| `tests/test_printers.py` | 18 | 无 |
| `tests/test_platform_dispatch.py` | 20 | 无 |
| `tests/test_server.py` | 22 | 无 |
| `tests/test_cups_e2e.py` | 3 | `lpadmin` 权限 |
| `tests/web_test.html` | 36 | Chrome + 服务端 |
| `tests/ui_test.html` | 22 | Chrome + 服务端 |
| `tests/apk_sim.html` | 12 | Chrome + 服务端 |

```bash
./tests/run_all.sh
```

---

## 过程中发现并修掉的问题

值得列出来,因为**每一个都不报错** —— 有几个还会给出一个看着挺正常的结果。

| # | 问题 | 如果发出去的后果 |
|---|---|---|
| 1 | `lpstat` 输出是本地化的,而解析器在找英文单词 `printer` | 中文 macOS 上**一台打印机都找不到**,而且是静默的。你的机器上有 4 台,含两台 80mm 热敏机 |
| 2 | `{{LANG}}` 由服务端替换,但 APK 里的文件是原样复制的 | `JSON.parse('{{LANG}}')` 在 app.js 第一行抛异常 —— **整个界面白屏**,且不报任何看得见的错 |
| 3 | Web UI 用相对路径调 API | APK 里每个请求都打到 WebView 自己身上而不是局域网服务端 —— **App 装得上但用不了** |
| 4 | `web/` 的资源按 `/web/*` 引用,但 APK 里它们在根目录 | 打包后样式和脚本全 404,而浏览器里一切正常 |
| 5 | 字体放在 `web/` 之外 | 没被打进 APK —— 中文会回退到系统字体,**破坏跨平台渲染一致性** |
| 6 | `android.R.drawable.stat_sys_data_sync` 是隐藏资源 | Java 编译报错;只有拿真 Android SDK 编译才会暴露 |
| 7 | XML 注释里写了 `----` | XML 非法,`mergeDebugResources` 失败 |
| 8 | 继承了 `SimpleHTTPRequestHandler` 的目录提供行为 | **`settings.json` 可以通过 HTTP 直接读走 —— 里面有 DeepSeek API key。** 从 Beta 继承而来;而这个服务的设计前提就是局域网可达 |
| 9 | 出图步骤把填充色设成白色后忘了改回来 | 出图结果**尺寸完全正确,内容纯白** |

第 8 条是安全问题,也是这批里最要紧的一个。第 2–5 条任何一条都会让 Android 端彻底不可用。

---

## 没有验证的部分 —— 如实列出

- **没有碰过任何打印机。** 字节层面的正确性有测试覆盖,纸面上的正确性没有。这个区别很重要。
- **蓝牙 SPP** —— 配对、连接、分块节奏、断线重连都实现了,但从未在真机上跑过。
- **Android 12/13/14 权限流程** —— 按 API 等级做了分支,未在真机验证。
- **前台服务跨 Doze** —— 未验证。
- **Windows** —— spooler 调用(`OpenPrinterW`、`StartDocPrinterW`、`WritePrinter`)在 macOS 上既编译不了也跑不了。载荷构造有测试覆盖,API 调用没有。
- **Linux** —— CUPS 路径与 macOS 共用,已在 macOS 上跑通,但发行版差异和 CUPS 版本差异仍需真实 Linux 验证。
- **打印保真度** —— 曲线图在真实热敏纸上、203dpi 下是否清晰可读,未知。

---

## 下一步

1. **接一台真 80mm 打印机试** —— 先 macOS。`--list-printers`,然后在卡片上按打印。这是价值最高的一步。
2. **看清晰度。** 576 点宽度下这张图信息很密。拿到真实纸张之后,字号可能得调大。
3. **Android 上真机跑一遍** —— 配对打印机、打测试页、再确认切后台之后前台服务还活着。
4. **Windows/Linux** —— 各自平台构建一次,再真实打印一次。
5. **考虑加打印预览。** 绘制现在在客户端,加预览几乎是白送的,而且能在浪费纸之前就看出纸张尺寸对不对。

## 已知的粗糙之处

- APK 是 **debug** 构建 —— 未做发布签名,且固定信任 debug keystore。发正式版需要配置签名。
- 应用名散在三个地方(`capacitor.config.json`、`strings.xml`、清单里的 label)。`strings.xml` 的覆盖让结果确定,但三者仍可能走偏。
- `printers/android_printer.py`(服务端桥接到同机 Android App 的那条路径)没有端到端跑过;它只在「Python 服务端本身跑在 Android 上」时才用得到,而那不是常规部署方式。
