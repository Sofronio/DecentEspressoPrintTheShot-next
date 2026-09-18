# PrintTheShot Next

中文 | [English](README.md)

> ⚠️ **测试状态**:软件层面已验证 —— 打印层、HTTP 服务端、Canvas 绘制、Web 界面共 **94 项自动化测试全部通过**。但 **尚未与真实热敏打印机或真实 DE1 联调**,因为手边暂时没有打印机。ESC/POS 字节布局、PBM/BMP 输出、CUPS/蓝牙调用路径都有测试覆盖 —— 不过覆盖的是「字节对不对」,不是「纸上印出来对不对」。

## 相比 Beta 改了什么

Beta 用 Pillow 在服务端画图,然后把生成的 PNG 送去打印。Next 把绘制整个搬进浏览器:

| | Beta | Next |
|---|---|---|
| 图表绘制 | Pillow(`ImageDraw`),服务端出 PNG | **浏览器 Canvas**,磁盘上不再有 PNG |
| 服务端依赖 | pillow | **纯标准库** |
| 启动 | 加载字体 + Pillow | **约 0.17 秒**,不 import 任何第三方库 |
| 绘制代码份数 | 1 份(Python) | **1 份(`web/render.js`)**,macOS 与 Android 共用 |
| 打印层 | 一个 `print_image()` 函数 | **`printers/` 包**,每平台一个适配器 |
| 打印触发 | 服务端渲染完就打印 | 服务端入队,**持有打印机的那一端负责渲染和打印** |
| 切换语言 | 每种语言重渲染一张 PNG | **按数据重画**,即时生效,不需要图片缓存 |
| 平台 | macOS / Linux / Windows | **macOS / Android**,外加 Linux 与 Windows 适配器 |

最关键的一点:**绘制代码只有一份**。macOS 和 Android 共用它,所以两端不可能画得不一样。ESC/POS 的字节同理在 JS 里拼装,Android 原生层不重复实现协议。

## 下载与运行

```bash
# 源码运行 —— 不需要安装任何依赖
python3 print_the_shot_server.py          # 默认 8000 端口
```

浏览器打开 `http://localhost:8000` 就是管理界面。

没有 `pip install` 这一步:绘制在浏览器里做,打印层调用的是系统本来就有的工具(macOS/Linux 的 CUPS、Windows 的打印后台)。

## 架构

```
浏览器 / Android WebView                        ← 持有打印机的那一端
├── web/render.js      Canvas 绘制(唯一的绘制实现)
├── web/printer.js     选路径:HTTP 或原生蓝牙
└── web/app.js         界面逻辑
        │  HTTP                          │  Capacitor 插件
        │  POST /api/print {bitmap}      │  printRaw(bytes) → 蓝牙 socket
        ▼                                ▼
Python 服务端                           Android 原生层
├── 接收 shot JSON                      └── 把 ESC/POS 字节写进 socket
├── 持久化历史(index.json)                 (不含协议逻辑 —— 原因见下)
├── 把数据发给前端
└── 把位图转交给 printers/
        │
        ▼
printers/  ── mac_printer.py     CUPS(lp/lpstat),PBM 或 ESC/POS
           ├─ linux_printer.py  CUPS,同一份代码
           ├─ windows_printer.py ctypes 调打印后台,ESC/POS 或 BMP
           └─ android_printer.py 桥接到原生层
```

### 绘制在哪里结束,打印从哪里开始

接缝是一次函数调用、一份数据契约:

```
canvasToBitmap(canvas)  →  { width, height, bytesPerRow, data }
                           1-bit、每行 MSB-first 打包、补齐到整字节
```

这正好就是 ESC/POS `GS v 0` 要的格式 —— 所以从浏览器到打印机,中间不需要任何重新编码。服务端也从不窥探位图内部:base64 解码、校验长度、转交适配器。服务端在打印这件事上的全部参与就这些。

### 为什么 ESC/POS 字节在 JavaScript 里拼

Android 原生插件当然可以自己拼 `GS v 0` 指令。它刻意不那么做。如果协议实现两份 —— 桌面打印一份 JS、蓝牙一份 Java —— 两份迟早会走偏,而症状是「只有 Android 打出来是乱的」,在桌面上根本看不见。只留一份实现,这类 bug 就不可能发生;代价是原生层退化成一个纯粹的字节管道。这个交换是划算的。

## Web 界面说明

- **状态卡片**:运行状态、收到的 shot 数、打印开关、豆子信息开关
- **打印卡片**(新增):平台、通道、打印机选择、模式、点数宽度 —— 改动立即生效
- **最近数据**:默认显示今天;日期下拉 + ◀ ▶ 前后日;每页 9/18/36;每张卡片是 **Canvas 缩略图**,滚动到视口才绘制
- **大图查看**:点缩略图;语言 chips 切换是瞬间的,因为重画在本地
- **单条操作**:打印 / 下载 JSON / 导出 PNG / 翻译(🌐)
- **统计**:按日期、冲煮方案分布、咖啡豆分布
- **上传**:拖放 JSON 文件
- **插件下载**:本地版 / GitHub 最新 / TXT 版(安卓蓝牙常拒绝 `.tcl`,传 `tcl.txt` 没问题)
- **服务更新**:检查 → 从 GitHub 更新,自动备份到 `backup/`

## 打印

完整说明见 **[docs/PRINTING_zh.md](docs/PRINTING_zh.md)**。简表:

| 平台 | 通道 | 模式 |
|---|---|---|
| macOS | CUPS(`lp`) | `driver`(PBM 走打印机驱动)· `raw`(ESC/POS 直通) |
| Linux | CUPS(`lp`) | 同 macOS |
| Windows | ctypes 调打印后台 | `escpos`(RAW,推荐)· `bmp`(兼容旧版) |
| Android | 蓝牙 SPP | 仅 ESC/POS |

```bash
python3 print_the_shot_server.py --list-printers    # 这台机器能看到哪些打印机?
python3 print_the_shot_server.py --print-mode raw   # 覆盖打印模式
```

## 测试

```bash
./tests/run_all.sh          # 全部
```

| 测试 | 项数 | 需要什么 |
|---|---|---|
| `tests/test_printers.py` | 18 | 无 —— 纯字节布局逻辑 |
| `tests/test_server.py` | 18 | 无 —— 自己起一个真服务端进程 |
| `tests/web_test.html` | 36 | Chrome + 一个跑着的服务端 |
| `tests/ui_test.html` | 22 | Chrome + 一个跑着的服务端 |

打印层与服务端两组在任何机器上都能跑。浏览器两组用真实无头 Chrome 走 DevTools 协议驱动(`tests/run_web_tests.mjs`);机器上没有 Chrome 时会**明确提示跳过,而不是默默算通过**。

## 目录结构

```
print_the_shot_server.py    # 数据中转 + 打印调度(不绘制)
printers/
  base.py                   # BasePrinter 接口 + 格式转换
  escpos.py                 # GS v 0 光栅位图,各平台共用
  mac_printer.py            # macOS(CUPS)
  linux_printer.py          # Linux(CUPS,同一份代码)
  windows_printer.py        # Windows(ctypes 调打印后台)
  android_printer.py        # 桥接到原生蓝牙层
  __init__.py               # 平台探测 + 适配器装载
web/
  index.html                # 界面模板({{LANG}} / {{VERSION}})
  render.js                 # ★ 唯一的绘制实现
  printer.js                # 前端打印调度
  app.js                    # 界面逻辑
  style.css
  render.test.html          # 独立的绘制测试页
android/                    # Capacitor 工程 + 原生蓝牙插件
tests/                      # 四组测试
fonts/                      # 内置 Noto Sans CJK SC(SIL OFL)
plugin/plugin.tcl           # DE1 插件(未改动,仍兼容 v1.6)
scripts/                    # 构建脚本 + PyInstaller spec
sample_shots/               # 示例数据
docs/                       # 打印指南、CI 说明、变更日志
shots_data/                 # 运行时:上传的 JSON + index.json
```

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/upload?machine_id=...` | 接收 shot JSON;落盘、记历史、入待打印队列 |
| GET | `/api/status` | 服务端状态 |
| GET | `/api/shots[?date=YYYY-MM-DD]` | 数据列表、可用日期、待打印项 |
| GET | `/api/shot?file=…&lang=…` | **供前端绘制的完整 shot JSON** |
| GET | `/api/stats` | 统计 |
| POST | `/api/print` | **打印 base64 编码的 1-bit 位图** |
| GET | `/api/printers` | 当前平台的打印机列表 |
| GET · POST | `/api/print/config` | 读取 / 写入打印设置 |
| GET | `/api/print-queue` | 等待打印的 shot |
| POST | `/api/print-queue/ack` | 打印完成回执 |
| GET · DELETE | `/api/queue` | 打印记录 / 清空 |
| GET | `/api/settings` `/api/language` `/api/languages` | 设置与语言 |
| POST | `/api/settings/beaninfo` `/api/settings/print` | 开关 |
| POST | `/api/translate/shot` | 翻译一条 shot 的文案(写回数据文件) |
| GET | `/download/json/*` | JSON 下载 |
| GET | `/plugin/plugin.tcl` `.txt` | 插件下载 |
| GET · POST | `/api/update/check` `/api/update` | 服务更新 |

已移除:`GET /images/*.png`(不再有图片)和 `python print_the_shot_server.py --render`(服务端已经没有可渲染的东西了)。

## 没有打印机也能验证

打印链路可以在完全没有硬件的情况下走通:

```bash
python3 print_the_shot_server.py --port 8780 &
curl -X POST "http://localhost:8780/upload?machine_id=TEST" \
     -H 'Content-Type: application/json' \
     --data-binary @sample_shots/prodigal_el_rafugio.json
python3 tests/test_server.py
```

`tests/test_server.py` 会往 `/api/print` 发一张真实位图,检查请求被接受、位图通过校验,以及失败时**带着原因**被如实报告,而不是被吞掉。

## 故障排查

| 现象 | 处理 |
|---|---|
| 打印卡片显示 ⚠️ | 当前平台没有可用适配器,跑 `--list-printers` 看看 |
| 提示 "no printer found" | 先在系统里添加打印机(macOS/Linux 用 CUPS,Windows 用设置) |
| 提示打印成功但没出纸 | 看打印队列:`lpstat -o`。Raw 队列可以试 `--print-mode raw` |
| 缩略图空白 | 缩略图是滚动到才画;看浏览器控制台里 `/api/shot` 是不是失败了 |
| 字体不对 | 内置字体要从 `/fonts/` 加载,检查是不是 404 |
| 更新没生效 | 更新后需要**重启服务端** |

## 许可证

GPLv3,与原项目一致。内置的 Noto Sans CJK 字体采用 SIL Open Font License 1.1,可自由再分发。
