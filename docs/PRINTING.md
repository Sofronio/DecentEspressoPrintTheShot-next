# Printing Guide

[中文](PRINTING_zh.md) | English

Everything about getting a chart onto paper. The rendering side is described in
[the README](../README.md); this document is only about bytes leaving the machine.

---

## The data contract

Every platform adapter receives the same thing:

```
bitmap (bytes) — 1-bit monochrome
                 rows packed MSB-first, left to right
                 each row padded up to a whole number of bytes
                 a set bit means a black dot
width, height  — in dots
```

Expected length is exactly `ceil(width / 8) * height`. Every adapter validates this
before touching a printer. A mismatch is rejected with a clear error rather than
printed, because a length mismatch produces a **skewed sheet**, not a crash, and a
skewed sheet is very hard to diagnose after the fact.

This format is chosen so that nothing has to be re-encoded anywhere:

| Consumer | Why it fits |
|---|---|
| ESC/POS `GS v 0` | wants exactly this layout |
| PBM (P4) | same bit order, only a header to add |
| BMP (1-bit) | same data, needs 4-byte row padding and bottom-up order |

`printers/escpos.py`, `bitmap_to_pbm()` and `bitmap_to_bmp1()` are the three
converters, and each one is covered by tests that check the actual bytes.

---

## macOS

**Adapter**: `printers/mac_printer.py` — CUPS via `lp` / `lpr` / `lpstat`.

macOS ships CUPS, so there is nothing to install. Two modes:

### `driver` (default)

The bitmap is written to a temporary **PBM (P4)** file and handed to `lp` with the
custom paper size and `fit-to-page`:

```
lp -d <printer> -o media=Custom.80x180mm -o fit-to-page \
   -o margin-top=0 -o margin-bottom=0 -o margin-left=0 -o margin-right=0 <file.pbm>
```

Use this when the printer has a real driver installed. CUPS rasterises the PBM into
whatever the device understands.

### `raw`

The bitmap is packed into ESC/POS and passed through untouched:

```
lp -d <printer> -o raw <file.bin>
```

Use this when the printer is configured as a **Raw queue** and interprets ESC/POS
itself. This path produces byte-for-byte the same output as the Android Bluetooth
path, so both platforms print the same sheet.

### Choosing a printer

```bash
python3 print_the_shot_server.py --list-printers
```

Or pick one in the web UI's **Printing** card. Leaving it on *System default* uses
whatever `lpstat -d` reports.

---

## Linux

**Adapter**: `printers/linux_printer.py` — identical to macOS, and it is literally
the same code (`LinuxPrinter` subclasses `MacPrinter`). CUPS is CUPS.

Differences worth knowing:

- Some minimal distributions ship `lpr` but not `lp`; the adapter falls back.
- On ARM boards (Raspberry Pi and friends) CUPS usually has to be installed by hand.
  `is_available()` reports `False` honestly rather than failing later at print time.

---

## Windows

**Adapter**: `printers/windows_printer.py` — the print spooler through plain
`ctypes`, no `pywin32`.

### `escpos` (default)

The bitmap is packed into ESC/POS and queued with the `RAW` datatype, so the bytes
bypass the driver and reach the device untouched. This is the standard way to drive
a thermal receipt printer on Windows, and it is the same byte stream the Android
path sends.

### `bmp` (legacy)

Wraps the bitmap as a 1-bit BMP and queues it as-is, reproducing the previous
version's byte layout. Only needed if your existing setup really did print that way.

> **Not verified on hardware.** Both modes are implemented and the BMP encoder is
> unit-tested (row padding, bottom-up order, palette), but neither has been run
> against a real Windows printer.

---

## Android

Android is structurally different: **the server is usually not involved at all.**

```
WebView (Capacitor)
  └── web/printer.js  →  assembles the full ESC/POS job
        └── Capacitor plugin PrintTheShotPrinter.printRaw({ data, address })
              └── Bluetooth SPP socket → printer
```

The JS side builds the complete byte stream — reset, `GS v 0`, feed, cut — and the
native layer writes it to the socket without inspection. See
[android/README.md](../android/README.md) for the plugin's methods and permissions.

### Transport

Classic Bluetooth **SPP**, UUID `00001101-0000-1000-8000-00805F9B34FB`. That is the
standard for thermal receipt printers; BLE is not used because most of these devices
expose SPP, not GATT.

### Android 12+ permissions

`BLUETOOTH_CONNECT` and `BLUETOOTH_SCAN` must be requested at runtime on API 31+.
Older versions use `BLUETOOTH` / `BLUETOOTH_ADMIN`. The plugin's
`requestPermissions()` handles both.

### Keeping the process alive

A foreground service with a notification (`foregroundServiceType="dataSync"` on
API 34+), so a print triggered by an incoming upload still happens when the app is
backgrounded.

### When a server runs on Android

If the Python service itself runs on Android (Termux, an embedded Python), the print
request needs an exit to the native Bluetooth layer. `printers/android_printer.py`
provides two bridges:

1. **HTTP bridge** (default) — the app listens on `127.0.0.1:9100` and the service
   POSTs the packed ESC/POS bytes to it.
2. **pyjnius** — a direct static call, tried only when the HTTP bridge is unreachable.

---

## Bitmap width

`print_width` (default **576**) is the number of dots across the paper. Common values:

| Paper | Printable width | Typical dots @203dpi |
|---|---|---|
| 80 mm | 72 mm | **576** (also 512 on some units) |
| 58 mm | 48 mm | 384 |

Getting this wrong is not catastrophic — the printer scales or clips — but it is the
first thing to check if output looks stretched or cropped.

---

## Configuration

Stored under `settings.print` and editable in the web UI or via the API.

| Key | Default | Meaning |
|---|---|---|
| `printer` | `""` | printer id; empty means the system default |
| `mode` | `""` | `driver` / `raw` / `escpos` / `bmp`; empty means the adapter default |
| `media` | `Custom.80x180mm` | CUPS paper size (macOS/Linux) |
| `print_width` | `576` | print width in dots |
| `feed_lines` | `3` | lines fed after the bitmap |
| `cut` | `true` | send a partial cut; disable on portable units that reject it |
| `threshold` | `200` | binarisation threshold (luminance above this is white) |
| `rotate` | `true` | rotate 90° — a receipt is narrower than the chart is wide |

```bash
curl http://localhost:8000/api/print/config
curl -X POST http://localhost:8000/api/print/config \
     -H 'Content-Type: application/json' -d '{"print_width": 512}'
```

Changing configuration reloads the adapter immediately; no restart needed.

---

## Debugging without a printer

The whole path can be exercised with no hardware attached:

```bash
python3 print_the_shot_server.py --port 8780 &
curl -X POST "http://localhost:8780/upload?machine_id=TEST" \
     -H 'Content-Type: application/json' \
     --data-binary @sample_shots/prodigal_el_rafugio.json
```

Then open `http://localhost:8780` and press print on a card. What to look for:

- **Printing card** turns red with a reason — that reason is the adapter's own
  message, e.g. *"no printer found; add one in System Settings first"*.
- **Print queue** shows the job with ✓ or ✗. A ✗ entry carries the failure text.

A failure that explains itself is deliberate: with no printer attached, "it didn't
work" is useless, and the whole point of passing the adapter's message through is
that you can tell *which* thing is missing.

To inspect the bytes without a printer, decode them yourself:

```python
import base64, sys
sys.path.insert(0, '.')
from printers import escpos

raw = base64.b64decode(open('job.b64').read())
print(raw[:8].hex(' '))     # 1d 76 30 00 <xL> <xH> <yL> <yH>
```

`xL/xH` are the width in **bytes**, `yL/yH` the height in **dots**. Mixing those two
units up is the single most common ESC/POS bug, which is why the test suite asserts
on it explicitly.

---

## Known gaps

- No real-hardware verification on any platform. No printer was available.
- Bluetooth scanning, pairing and socket keep-alive are implemented but unexercised.
- The Android foreground service has not been through a real background/Doze cycle.
