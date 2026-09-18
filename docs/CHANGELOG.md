# Changelog

[中文](CHANGELOG_zh.md) | English

## 2.1-next.1

The release that moved drawing out of the server and made the printing layer
pluggable. This is a structural change rather than a feature release: almost
everything below is a consequence of one decision — **the browser draws, the server
relays and dispatches.**

### Architecture

- **Drawing moved to the browser.** `web/render.js` is now the only renderer in the
  project. It is a direct translation of the old Pillow `ImageDraw` code: every
  geometry constant maps 1:1, and the maths is unchanged. The text anchors were
  reimplemented on Canvas with Pillow's semantics (`la` / `rm` / `lm` / `mt`)
  rather than Canvas defaults, so sizes, baselines and alignment match the previous
  output instead of merely resembling it.
- **The server no longer produces images.** All PNG output, the `shots_images/`
  directory, `/images/*.png` and the `--render` CLI flag are gone. The server is
  now a data relay and a print dispatcher, and nothing else.
- **`printers/` is a pluggable package.** `BasePrinter` defines the interface;
  `load_printer()` picks an adapter from `sys.platform`. Upper layers call the
  interface and never learn which platform is underneath.
- **Printing is triggered by the end that owns the printer.** The server queues an
  arriving shot; the browser (or Android WebView) claims it, renders it, prints it,
  and acknowledges. Previously the server rendered and printed on its own.

### Added

- `web/render.js` — `renderShotToCanvas()`, `canvasToBitmap()`, `bitmapToBase64()`
- `web/printer.js` — client-side dispatch: HTTP on desktop, native Bluetooth on Android
- `web/render.test.html` — standalone renderer test page, no server needed
- `printers/base.py` — the interface, plus `bitmap_to_pbm()` and `bitmap_to_bmp1()`
- `printers/escpos.py` — `GS v 0` raster bitmap, shared by every platform
- `printers/mac_printer.py`, `linux_printer.py`, `windows_printer.py`, `android_printer.py`
- `api/print` now takes a base64 1-bit bitmap instead of a filename
- `api/shot` — full shot JSON for the front end to render
- `api/printers`, `api/print/config` (GET/POST), `api/print-queue`, `api/print-queue/ack`
- `--list-printers` and `--print-mode` CLI options
- A **Printing** card in the web UI: platform, transport, printer picker, mode, dot width
- `tests/` — 133 tests across eight suites

### Changed

- The print path is negotiated, not assumed: `driver` vs `raw` on CUPS, `escpos`
  vs `bmp` on Windows.
- Print failures now carry the adapter's own message through to the UI. With no
  printer attached, "it didn't work" is useless.
- `web/index.html` was split into `index.html` + `app.js` + `style.css`. Asset
  paths are relative and the server mounts `web/` at the root as well, so the same
  markup resolves correctly both in a browser and inside the APK (where Capacitor
  puts the assets at the root).
- Language switching redraws from data instead of re-rendering a PNG per language,
  so the image cache and the background re-render thread are both gone.
- Service update now replaces the whole `web/` tree, not just `index.html`.

### Fixed

- Thumbnail blankness is no longer indistinguishable from a black chart. The
  browser tests count only **opaque** dark pixels, so an undrawn canvas (all
  transparent) no longer reads as 100% ink.
- `/api/shot` rejects traversal probes and non-`.json` names.
- Booleans in print config survive a round trip.

### Removed

- Pillow, and with it the entire `pip install` step — **the server now uses the
  standard library only.**
- `render_chart()`, `generate_print_image()`, `print_image()`, `windows_print_bmp()`
  and the Pillow drawing helpers
- `smart_wrap_text()` (unused, and superseded by `wrap_by_width()`)
- `GET /images/*.png` and `python print_the_shot_server.py --render`

### Android

- A Capacitor shell (`android/`) that bundles this same web UI into an APK and
  prints over Bluetooth ESC/POS through a native plugin.
- **The ESC/POS protocol is assembled in JavaScript, not in Java.** The native
  layer writes bytes to the socket and nothing else. Implementing it twice — once
  in JS for desktop, once in Java for Bluetooth — guarantees the two drift, and the
  symptom would be "Android prints wrong, and only Android", invisible from the
  desktop.
- **No vendor SDK**, deliberately. See the reasoning in
  [android/README.md](../android/README.md).
- A first-run screen for the server address (the web UI is bundled, so it has no
  idea where the LAN server is) and a Bluetooth settings screen (permission, device
  list, connection status, test print).
- `scripts/build_android.sh` builds an APK from the repository in one command.

### Security

- **`settings.json` was readable over HTTP.** The server inherited
  `SimpleHTTPRequestHandler`'s directory serving, so any file next to the server —
  including the DeepSeek API key — could be fetched by anyone on the LAN, and this
  service is *designed* to be LAN-reachable. Inherited from Beta. Replaced with an
  explicit whitelist: anything not matched returns 404.

### Verification

| Suite | Tests | Coverage |
|---|---|---|
| front-end JS syntax | 5 files | one bad paren = a blank UI |
| `tests/test_printers.py` | 18 | ESC/POS header, PBM/BMP encoding, validation |
| `tests/test_platform_dispatch.py` | 20 | platform detection, localised `lpstat`, Windows payload |
| `tests/test_server.py` | 22 | HTTP API, upload → history → queue, dispatch, exposure |
| `tests/test_cups_e2e.py` | 3 | **a real CUPS round trip**, bytes compared |
| `tests/web_test.html` | 36 | renderer maths, bitmap packing, the whole print API |
| `tests/ui_test.html` | 22 | the real UI in a real browser |
| `tests/apk_sim.html` | 12 | the APK code path (bundled assets, server address) |

133 tests in total. Server startup measured at **0.165 s** to serve.

The CUPS suite is the strongest of these: it creates a virtual queue pointing at a
local listener, submits a real job through the adapter, and compares the bytes CUPS
emits against the bytes that went in — byte for byte, in both `driver` and `raw`
mode. That is as close to a real printer as this environment gets.

**Not verified**: no printer was available, so nothing has touched hardware. Byte
layouts are asserted; paper is not.

## Inherited from 2.0-beta.2

Everything from the Beta line is carried over unchanged: DeepSeek translation,
bilingual UI, bundled Noto Sans CJK SC, history persistence, date filter and
pagination, statistics, plugin distribution (local / GitHub / TXT), and the
self-update mechanism.
