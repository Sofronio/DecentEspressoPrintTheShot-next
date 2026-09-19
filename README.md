# PrintTheShot Next

[中文文档](README_zh.md) | English

> ⚠️ **Test status**: verified at the software level — 133 automated tests pass across the printing layer, the HTTP server, the Canvas renderer and the web UI. **Nothing has been verified against a real thermal printer or a real DE1**, because no printer is currently on hand. The ESC/POS byte layout, the PBM/BMP output and the CUPS/Bluetooth call paths are all covered by tests that check the bytes, not the paper.

## What changed from Beta

Beta drew charts on the server with Pillow and printed the resulting PNG. Next moves drawing into the browser entirely:

| | Beta | Next |
|---|---|---|
| Chart drawing | Pillow (`ImageDraw`), server-side PNG | **Canvas in the browser**, no PNG on disk |
| Server dependencies | pillow | **standard library only** |
| Startup | loads a font + Pillow | **~0.17 s**, imports nothing third-party |
| Renderer count | 1 (Python) | **1 (`web/render.js`)**, shared by macOS and Android |
| Print layer | one `print_image()` function | **`printers/` package**, one adapter per platform |
| Print trigger | server renders then prints | server queues, **the end that owns the printer renders and prints** |
| Language switching | re-render a PNG per language | **redraw from data**, instant, no image cache |
| Platforms | macOS / Linux / Windows | **macOS / Android**, plus Linux and Windows adapters |

The single most important consequence: **there is exactly one renderer**. macOS and Android share it, so the two platforms cannot drift apart visually — and the ESC/POS bytes are assembled in JS for the same reason, so the Android native layer never reimplements the protocol.

## Download & Run

### Pre-built downloads

Grab your platform's file from the [Releases page](https://github.com/Sofronio/DecentEspressoPrintTheShot-next/releases):

| Platform | File | How to run |
|---|---|---|
| macOS (Apple Silicon) | `PrintTheShotNext-macos-arm64.zip` | unzip → **allow it once (below)** → double-click |
| macOS (Intel) | `PrintTheShotNext-macos-intel.zip` | same |
| Windows | `PrintTheShotNext-windows-x64.exe` | double-click |
| Linux | `PrintTheShotNext-linux` | `chmod +x PrintTheShotNext-linux && ./PrintTheShotNext-linux` |
| Android | `PrintTheShotNext-android.apk` | install the APK, then point it at the server (it asks on first run) |

The desktop builds are **complete servers**: run one and open `http://localhost:8000`.
The Android app is a **client** — it has no server of its own; it needs a desktop (or
any machine) running the server on the same network, then prints over Bluetooth.

### From source

```bash
# from source — no dependencies to install
python3 print_the_shot_server.py          # default port 8000
```

Open `http://localhost:8000` for the management UI.

There is no `pip install` step: rendering happens in the browser and the printing layer shells out to tools your OS already ships (CUPS on macOS/Linux, the print spooler on Windows).

> ### macOS packaged build: allow it once before the first launch
>
> The app is not notarized (that needs a paid Apple Developer account), so macOS
> blocks the first launch. Two ways, either works:
>
> **Option A — one command** (after moving the app to `/Applications`):
>
> ```bash
> xattr -dr com.apple.quarantine /Applications/PrintTheShot.app
> ```
>
> **Option B — through the UI:** try to open it once, then go to
> **System Settings → Privacy & Security**, scroll to *Security*, and click
> **Open Anyway**.
>
> After that it launches normally, every time. You only do this once per download.
>
> > ⚠️ Do not confuse this with *"the app is damaged and can't be opened"*. That is a
> > different failure: the code signature itself did not validate, and **neither
> > option above will help** — not even right-click → Open. It means the build
> > predates the signing fix; use a current release.

## Architecture

```
Browser / Android WebView                       ← the end that owns the printer
├── web/render.js      Canvas drawing (the only renderer)
├── web/printer.js     pick a path: HTTP or native Bluetooth
└── web/app.js         UI
        │  HTTP                          │  Capacitor plugin
        │  POST /api/print {bitmap}      │  printRaw(bytes) → Bluetooth socket
        ▼                                ▼
Python server                           Android native layer
├── receive shot JSON                   └── writes ESC/POS bytes to the socket
├── persist history (index.json)            (no protocol logic — see below)
├── serve data to the front end
└── dispatch the bitmap to printers/
        │
        ▼
printers/  ── mac_printer.py     CUPS (lp/lpstat), PBM or ESC/POS
           ├─ linux_printer.py  CUPS, same code
           ├─ windows_printer.py spooler via ctypes, ESC/POS or BMP
           └─ android_printer.py bridge to the native layer
```

### Where rendering ends and printing begins

The seam is one function call and one data contract:

```
canvasToBitmap(canvas)  →  { width, height, bytesPerRow, data }
                           1-bit, rows packed MSB-first, padded to whole bytes
```

That is exactly what ESC/POS `GS v 0` wants, which is why nothing has to be re-encoded between the browser and the printer. The server never looks inside the bitmap — it base64-decodes, validates the length, and hands it to the adapter. That is the whole of the server's involvement in printing.

### Why the ESC/POS bytes are built in JavaScript

A native Android plugin could assemble the `GS v 0` command itself. It deliberately does not. If the protocol were implemented twice — once in JS for desktop printing and once in Java for Bluetooth — the two would drift, and the symptom would be an Android-only garbled print that is invisible from the desktop. Keeping one implementation makes that class of bug impossible, at the cost of the native layer being a dumb byte pipe. That is the right trade.

### Running it as a packaged app

A packaged build behaves differently from a source run, in three ways worth knowing:

- **The web UI opens by itself on start.** The app is a background service with no
  window and no Dock icon, so there is otherwise nothing to indicate it came up.
- **A red "Stop service" button** appears in the status card — but only in the
  **macOS** build. It is the one configuration with no other way to stop it: no Dock
  icon, no window, no terminal. Windows has a console window to close, and Linux is
  normally run from a terminal. Stop it from a terminal with
  `pkill -f PrintTheShot.app`.
- **A log is written** to `~/Library/Application Support/PrintTheShot/server.log`
  (the equivalent path on Windows and Linux). A packaged app has no terminal, so
  this file is the only place a startup failure will be visible.

> The shutdown endpoint is **not restricted by source IP**, so anything on the same
> network can reach it. Fine on a home LAN; on a shared network, tighten
> `_allow_shutdown()`.

## Web UI Guide

- **Status card**: running state, shots received, print toggle, bean-info toggle
- **Printing card** (new): platform, transport, printer picker, mode, dot width — changing any of these takes effect immediately
- **Recent data**: today's shots by default; date dropdown + ◀ ▶ day navigation; 9/18/36 per page; each card is a **Canvas thumbnail**, lazily drawn on scroll
- **Large view**: click a thumbnail; language chips switch instantly because redrawing is local
- **Per-shot actions**: print / download JSON / export PNG / translate (🌐)
- **Statistics**: by date, brew profile distribution, bean distribution
- **Upload**: drag & drop a JSON file
- **Plugin section**: local / GitHub / TXT version (Android often rejects `.tcl` over Bluetooth; `tcl.txt` transfers fine)
- **Service update**: check → update from GitHub, with automatic backup to `backup/`

## Printing

See **[docs/PRINTING.md](docs/PRINTING.md)** for the full guide. In short:

| Platform | Transport | Modes |
|---|---|---|
| macOS | CUPS via `lp` | `driver` (PBM through the printer driver) · `raw` (ESC/POS straight through) |
| Linux | CUPS via `lp` | same as macOS |
| Windows | spooler via ctypes | `escpos` (RAW, recommended) · `bmp` (legacy) |
| Android | Bluetooth SPP | ESC/POS only |

```bash
python3 print_the_shot_server.py --list-printers    # what can this machine see?
python3 print_the_shot_server.py --print-mode raw   # override the mode
```

## Testing

```bash
./tests/run_all.sh          # everything
```

| Suite | Count | Needs |
|---|---|---|
| front-end JS syntax | 5 files | `node` — one bad paren is a blank UI |
| `tests/test_printers.py` | 18 | nothing — pure byte-layout logic |
| `tests/test_platform_dispatch.py` | 20 | nothing |
| `tests/test_server.py` | 22 | nothing — spawns a real server |
| `tests/test_cups_e2e.py` | 3 | `lpadmin` rights — a real CUPS round trip |
| `tests/web_test.html` | 36 | Chrome + a running server |
| `tests/ui_test.html` | 22 | Chrome + a running server |
| `tests/apk_sim.html` | 12 | Chrome + a running server |

The printing and server suites run anywhere. The browser suites use real headless Chrome over the DevTools Protocol (`tests/run_web_tests.mjs`) and are skipped with an explicit warning — not a silent pass — when Chrome is absent.

## Directory Layout

```
print_the_shot_server.py    # data relay + print dispatch (no rendering)
printers/
  base.py                   # the BasePrinter interface + format converters
  escpos.py                 # GS v 0 raster bitmap, shared by every platform
  mac_printer.py            # macOS (CUPS)
  linux_printer.py          # Linux (CUPS, same code)
  windows_printer.py        # Windows (spooler via ctypes)
  android_printer.py        # bridge to the native Bluetooth layer
  __init__.py               # platform detection + adapter loading
web/
  index.html                # UI template ({{LANG}} / {{VERSION}})
  render.js                 # ★ the only renderer
  printer.js                # client-side print dispatch
  app.js                    # UI logic
  style.css
  render.test.html          # standalone renderer test page
android/                    # Capacitor project + native Bluetooth plugin
tests/                      # all four suites
web/fonts/                  # bundled Noto Sans CJK SC (SIL OFL; ships inside the APK)
plugin/plugin.tcl           # DE1 plugin (unchanged, still v1.6-compatible)
scripts/                    # build scripts + PyInstaller spec
sample_shots/               # sample data
docs/                       # printing guide, CI notes, changelog
shots_data/                 # runtime: uploaded JSON + index.json
```

## API

| Method | Path | Description |
|---|---|---|
| POST | `/upload?machine_id=...` | receive shot JSON; persists, queues for printing |
| GET | `/api/status` | server status |
| GET | `/api/shots[?date=YYYY-MM-DD]` | shot list, available dates, pending prints |
| GET | `/api/shot?file=…&lang=…` | **full shot JSON for rendering** |
| GET | `/api/stats` | statistics |
| POST | `/api/print` | **print a base64 1-bit bitmap** |
| GET | `/api/printers` | printers on this platform |
| GET · POST | `/api/print/config` | read / write print settings |
| GET | `/api/print-queue` | shots waiting to be printed |
| POST | `/api/print-queue/ack` | acknowledge a finished print |
| GET | `/api/queue` · DELETE | print history / clear it |
| GET | `/api/settings` `/api/language` `/api/languages` | settings & languages |
| POST | `/api/settings/beaninfo` `/api/settings/print` | toggles |
| POST | `/api/translate/shot` | translate a shot's text (writes back to the data file) |
| GET | `/download/json/*` | JSON download |
| GET | `/plugin/plugin.tcl` `.txt` | plugin download |
| POST | `/api/shutdown` | **stop the service** (not IP-restricted, see above) |
| GET · POST | `/api/update/check` `/api/update` | service update |

Removed: `GET /images/*.png` (there are no images any more) and `python print_the_shot_server.py --render` (there is nothing to render server-side).

## Printing without a printer

The print path can be exercised fully without hardware:

```bash
python3 print_the_shot_server.py --port 8780 &
curl -X POST "http://localhost:8780/upload?machine_id=TEST" \
     -H 'Content-Type: application/json' \
     --data-binary @sample_shots/prodigal_el_rafugio.json
python3 tests/test_server.py
```

`tests/test_server.py` posts a real bitmap to `/api/print` and checks that the request is accepted, that the bitmap passes validation, and that a failure is reported **with a reason** rather than swallowed.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Print card shows ⚠️ | No adapter available on this platform — check `--list-printers` |
| "no printer found" | Add a printer in the OS; CUPS on macOS/Linux, Settings on Windows |
| Print succeeds but nothing comes out | Check the printer queue: `lpstat -o`. Try `--print-mode raw` for a Raw queue |
| Thumbnails blank | They draw on scroll; check the browser console for a failed `/api/shot` |
| Font looks wrong | The bundled font must load from `/fonts/`; check for a 404 |
| Update has no effect | Updates need a **server restart** |
| **macOS: "cannot verify the developer"** | The app is not notarized. Allow it once: `xattr -dr com.apple.quarantine /Applications/PrintTheShot.app`, or **System Settings → Privacy & Security → Open Anyway**. |
| **macOS: "is damaged and can't be opened"** | **Not the same thing.** The code signature failed to validate and right-clicking will not help. `xattr` does **not** fix this either — you have a build from before the signing fix. Use a current release. |
| macOS: app opens but nothing happens | The packaged app used to crash when launched from Finder (it tried to create `shots_data/` next to a read-only working directory). Fixed; on an older build, run the binary from a terminal in a writable directory. |
| Where did my settings/data go? | Packaged builds store them in `~/Library/Application Support/PrintTheShot/` (the equivalent on Windows/Linux), **not** next to the app. Source runs use the current directory. |
| Can't find any window to close it | A packaged macOS build has no Dock icon and no window by design. Use the red **Stop service** button in the web UI, or `pkill -f PrintTheShot.app`. |

## License

GPLv3, same as the original project. The bundled Noto Sans CJK font is under the SIL Open Font License 1.1 and is freely redistributable.
