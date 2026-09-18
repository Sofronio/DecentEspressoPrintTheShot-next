# 打印指南

中文 | [English](PRINTING.md)

把一张曲线图弄到纸上的全部内容。绘制那一侧见 [README](../README_zh.md),这份文档只讲字节离开机器之后的事。

---

## 数据契约

每个平台适配器收到的都是同一样东西:

```
bitmap (bytes) — 1-bit 单色
                 每行 MSB-first 打包,从左到右
                 行宽向上补齐到整字节
                 位为 1 表示黑点
width, height  — 单位是点
```

期望长度恰好是 `ceil(width / 8) * height`。每个适配器在碰打印机之前都会先校验这一点。长度不符会被明确拒绝,而不是硬打出去 —— 因为长度不符的结果是一张**错位的纸**,不是一次崩溃,而错位的纸事后极难排查。

选这个格式,是为了让整条链路上不需要任何重新编码:

| 使用方 | 为什么正好合适 |
|---|---|
| ESC/POS `GS v 0` | 要的就是这个布局 |
| PBM(P4) | 位序相同,只需加个文件头 |
| BMP(1-bit) | 数据相同,需要 4 字节行对齐 + 自下而上的行序 |

`printers/escpos.py`、`bitmap_to_pbm()`、`bitmap_to_bmp1()` 就是这三个转换器,每一个都有检查真实字节的测试覆盖。

---

## macOS

**适配器**:`printers/mac_printer.py` —— 通过 `lp` / `lpr` / `lpstat` 走 CUPS。

macOS 自带 CUPS,不需要装任何东西。两种模式:

### `driver`(默认)

把位图写成临时 **PBM(P4)** 文件,连同自定义纸张与 `fit-to-page` 一起交给 `lp`:

```
lp -d <printer> -o media=Custom.80x180mm -o fit-to-page \
   -o margin-top=0 -o margin-bottom=0 -o margin-left=0 -o margin-right=0 <file.pbm>
```

打印机装了正经驱动时用这个。CUPS 会把 PBM 栅格化成设备认识的格式。

### `raw`

把位图打包成 ESC/POS 原样直通:

```
lp -d <printer> -o raw <file.bin>
```

打印机在系统里被配成 **Raw 队列**、由设备自己解释 ESC/POS 时用这个。这条路径与 Android 蓝牙路径产出逐字节相同的输出,所以两端打出来是同一张纸。

### 选打印机

```bash
python3 print_the_shot_server.py --list-printers
```

或者在 Web 界面的**打印**卡片里选。留在*系统默认*就是 `lpstat -d` 报的那台。

---

## Linux

**适配器**:`printers/linux_printer.py` —— 与 macOS 完全一致,而且就是同一份代码(`LinuxPrinter` 继承 `MacPrinter`)。CUPS 就是 CUPS。

几个值得知道的差异:

- 某些精简发行版只有 `lpr` 没有 `lp`,适配器会做回退。
- 在 ARM 板子(树莓派之类)上 CUPS 通常要手动装。`is_available()` 会如实返回 `False`,而不是等打印时才失败。

---

## Windows

**适配器**:`printers/windows_printer.py` —— 纯 `ctypes` 调打印后台,不依赖 `pywin32`。

### `escpos`(默认)

把位图打包成 ESC/POS,以 `RAW` 数据类型入队,字节绕过驱动直抵设备。这是 Windows 上驱动热敏小票机的标准做法,也是 Android 路径发的同一串字节。

### `bmp`(兼容旧版)

把位图包成 1-bit BMP 原样入队,复刻旧版的字节布局。只有在你原来的环境确实靠这种方式出纸时才需要。

> **未在真机上验证。** 两种模式都实现了,BMP 编码器也有单元测试(行对齐、自下而上行序、调色板),但都没在真实的 Windows 打印机上跑过。

---

## Android

Android 结构上不一样:**服务端通常根本不参与。**

```
WebView(Capacitor)
  └── web/printer.js  →  拼出完整的 ESC/POS 任务
        └── Capacitor 插件 PrintTheShotPrinter.printRaw({ data, address })
              └── 蓝牙 SPP socket → 打印机
```

JS 侧拼出完整字节流 —— 复位、`GS v 0`、走纸、切纸 —— 原生层只是把它写进 socket,不做任何检查。插件的方法与权限见 [android/README_zh.md](../android/README_zh.md)。

### 传输方式

经典蓝牙 **SPP**,UUID `00001101-0000-1000-8000-00805F9B34FB`。这是热敏小票机的标准;不用 BLE,是因为这类设备绝大多数只暴露 SPP 而不是 GATT。

### Android 12+ 权限

API 31+ 必须在运行时申请 `BLUETOOTH_CONNECT` 和 `BLUETOOTH_SCAN`。更低版本用 `BLUETOOTH` / `BLUETOOTH_ADMIN`。插件的 `requestPermissions()` 两种都处理。

### 进程保活

前台服务 + 通知(API 34+ 需要 `foregroundServiceType="dataSync"`),这样 App 切到后台时,由上传触发的打印仍然能完成。

### 服务端跑在 Android 上时

如果 Python 服务本身跑在 Android 上(Termux、嵌入式 Python),打印请求需要一个出口交给原生蓝牙层。`printers/android_printer.py` 提供两条桥:

1. **HTTP 桥**(默认)—— App 监听 `127.0.0.1:9100`,服务端把打包好的 ESC/POS 字节 POST 过去。
2. **pyjnius** —— 直接静态调用,只在 HTTP 桥不可达时才尝试。

---

## 位图宽度

`print_width`(默认 **576**)是横跨纸张的点数。常见取值:

| 纸张 | 可打印宽度 | 203dpi 下的常见点数 |
|---|---|---|
| 80 mm | 72 mm | **576**(部分机型 512) |
| 58 mm | 48 mm | 384 |

填错不会出大事(打印机会缩放或裁切),但输出看起来被拉伸或截断时,这是第一个该查的地方。

---

## 配置

存放在 `settings.print` 下,可在 Web 界面改,也可以走 API。

| 键 | 默认 | 含义 |
|---|---|---|
| `printer` | `""` | 打印机 id;空 = 系统默认 |
| `mode` | `""` | `driver` / `raw` / `escpos` / `bmp`;空 = 适配器默认 |
| `media` | `Custom.80x180mm` | CUPS 纸张尺寸(macOS/Linux) |
| `print_width` | `576` | 打印宽度(点) |
| `feed_lines` | `3` | 位图之后走纸行数 |
| `cut` | `true` | 发部分切纸;便携机不支持时关掉 |
| `threshold` | `200` | 二值化阈值(亮度高于此判为白) |
| `rotate` | `true` | 旋转 90° —— 小票比图窄 |

```bash
curl http://localhost:8000/api/print/config
curl -X POST http://localhost:8000/api/print/config \
     -H 'Content-Type: application/json' -d '{"print_width": 512}'
```

改完立即重新装载适配器,不用重启。

---

## 没有打印机时怎么调试

整条链路可以在完全没有硬件的情况下走一遍:

```bash
python3 print_the_shot_server.py --port 8780 &
curl -X POST "http://localhost:8780/upload?machine_id=TEST" \
     -H 'Content-Type: application/json' \
     --data-binary @sample_shots/prodigal_el_rafugio.json
```

然后打开 `http://localhost:8780`,在卡片上按打印。要看的是:

- **打印卡片**会变红并给出原因 —— 那个原因就是适配器自己的消息,比如*「未找到可用打印机,请先在系统设置里添加」*。
- **打印队列**会记下这条任务,✓ 或 ✗。✗ 的条目带着失败文本。

「失败必须说清楚原因」是刻意的:没接打印机的时候,「没成功」毫无用处 —— 把适配器的消息原样透出来的意义,就在于你能看出*缺的到底是哪一样东西*。

想在没打印机的情况下直接看字节,自己解一下:

```python
import base64, sys
sys.path.insert(0, '.')
from printers import escpos

raw = base64.b64decode(open('job.b64').read())
print(raw[:8].hex(' '))     # 1d 76 30 00 <xL> <xH> <yL> <yH>
```

`xL/xH` 是宽度,单位**字节**;`yL/yH` 是高度,单位**点**。把这两个单位搞混是 ESC/POS 最常见的 bug,所以测试里专门为它写了一条断言。

---

## 已知缺口

- 所有平台都没有经过真机验证 —— 手边没有打印机。
- 蓝牙扫描、配对、socket 保活都实现了,但没有实际跑过。
- Android 前台服务没有经历过真实的切后台 / Doze 周期。
