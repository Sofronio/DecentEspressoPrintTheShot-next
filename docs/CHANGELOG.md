# Changelog

[中文](CHANGELOG_zh.md) | English

## 2.1-beta.4

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

## 2.1-beta.3

The release that made the Android app usable as a background print node. Almost
everything here comes from one fact: a tablet whose app is not in the foreground is
**frozen by the system**, and the whole chain fails silently when it is.

### Changed

- **The background service is no longer optional, and its type changed.** Nothing in
  the app ever called `startForegroundService` — it existed in the plugin and in a doc
  comment and nowhere else, so the service could never start and the app was frozen the
  moment it went to the background. It now starts with the app, and there is a switch in
  the UI to turn it off.
- **`dataSync` → `connectedDevice`.** A Bluetooth printer is an external device, which
  is what `connectedDevice` means; more to the point, from Android 15 `dataSync` carries
  a 6-hour-per-24-hour cap and is stopped by the system when it expires, while
  `connectedDevice` is not among the types that limit applies to. Reading the job as
  "moving data" had quietly ruled out the only shape this service makes sense in.

### Fixed

- **Printing stopped in the background, and the queue was never drained.** The pump was
  a `setInterval` inside the WebView; once the WebView is hidden, Chromium throttles its
  timers to roughly once a minute and sometimes not at all. The server now pokes the
  front end when a shot arrives — an `evaluateJavascript` call, not a timer, so the
  throttling does not apply. Rendering still happens only in the front end.
- **The same receipt could print forever.** When a print succeeded but its
  acknowledgement did not arrive, the server kept the job and the next pass claimed it
  again. This was hit for real: the printer fed paper continuously. "Print each file
  once" is now this end's own invariant — recorded *before* the ack, so a lost ack
  cannot reprint it — with a per-pass cap as a backstop.
- **The same shot printed several times.** The DE1's `after_flow_complete` can fire more
  than once, and an upload that times out client-side still lands on the server, so the
  retry stores a second copy. Every upload gets a fresh filename, so name-based
  de-duplication caught none of it. De-duplication is now by **content** (SHA-256, 30
  second window) on both the Android and the desktop server, and the front end re-fetches
  the queue per job so the copies the server drops are dropped before they print.
- **The machine name printed as UNKNOWN while the UI showed de1xl.** The name is not in
  the shot file — that is the uploaded JSON verbatim — it lives in the server's index.
  It now travels on the queue job, and the manual print path passes it from the card it
  was already displaying.
- **The plugin downloads 404'd on the tablet.** `npx cap copy` only moves `webDir`, and
  `plugin/plugin.tcl` lives at the repository root, so it never entered the APK — while
  the desktop packages, whose spec lists it, were fine. The build now bundles it under
  both names (`plugin.tcl` and `plugin.tcl.txt`; Android often refuses `.tcl` over
  Bluetooth and accepts `.txt`).
- **The test suite printed to real hardware.** `tests/web_test.html` really POSTs
  `/api/print`, and its comment — "no printer is attached, that is fine" — only holds on
  a machine with no printer configured. With a thermal printer as the system default,
  every test run pushed a full chart (about 94 KB) at it; a small-buffer thermal printer
  cannot absorb that, loses alignment and feeds paper continuously, and the only way to
  stop it was to cut the power. This is worth stating plainly: the runaway paper during
  this release's development was the test suite, not the app. The tests now start the
  server with `PTS_PRINT_DRYRUN=1`, which swaps in an adapter that validates the bitmap
  and succeeds **without touching a printer**.

### Web UI

- The GitHub button opens the file on GitHub rather than the releases page — a button
  that says "download" should not land you on a page you have to search.
- The plugin steps are no longer double-numbered: the `<ol>` numbers them, and the
  strings carried their own "1. 2. 3." on top.
- Step four shows this machine's actual address rather than the words "this machine's
  IP", with the path beside it — two fields in the plugin, filled in one go.
- **The steps now say to create `/de1plus/plugins/print_the_shot/` when it is missing.**
  That folder does not exist on a fresh install, so the old wording left the user stuck
  at step one, wondering where the file was supposed to go.

### Verification

- On a Samsung SM-X210 running Android 16, printing to a **real Bluetooth thermal
  printer**: one upload produced exactly one receipt, the queue drained, and nothing
  printed again afterwards.
- The plugin downloads return 200 with byte-identical content to `plugin/plugin.tcl`.
- The full test suite passes, including four new de-duplication tests.

**Not verified**: the shots printed during testing came from a file rather than from a
real DE1, and nothing was printed through the desktop adapters — so CUPS, the Windows
spooler and the DE1's own upload path are still untested against paper.

## 2.1-beta.2

The release that made the Android app a server in its own right, reversing the client
shape shipped in 2.1-beta.1. The tablet now receives shots straight from the DE1,
renders them, and prints over Bluetooth — with no computer anywhere in the path.

### Changed

- **The Android app is now a complete server, not a client.** The previous build made
  the APK a client that connected to a desktop server; that was the wrong shape for
  this project, whose goal is a self-contained print node. Client mode and its
  server-address setup screen are gone entirely.
- **The routes mirror the desktop server**, so the same DE1 plugin configuration works
  against either end.

### Added

- `android/.../server/MiniHttpServer.java` — a hand-written HTTP server on
  `java.net.ServerSocket`, no dependencies. A library such as NanoHTTPD was rejected
  deliberately: the native sources are overlaid onto a Capacitor-generated project and
  this repository has no `build.gradle` in which to pin a dependency, so adding one
  would mean an implicit prerequisite that cannot be version-controlled.
- `android/.../server/AppServer.java` — routes: web UI, `/upload`, history, statistics,
  the pending-print queue, the printer list, `/api/print`.
- `android/.../server/ShotStore.java` — app-private storage (`getFilesDir`), so the app
  never requests a storage permission. Filenames carry a microsecond ID so two shots
  uploaded within the same second cannot collide.
- `android/.../server/DeviceInfo.java` — LAN address detection that enumerates
  interfaces and **excludes tunnel interfaces**, rather than the common "connect to
  8.8.8.8 and read back the source address" trick. That trick returns the VPN address
  whenever a VPN is up, and the user then types an address that cannot be reached — the
  desktop build hit exactly this.
- `android/.../server/ServerHolder.java` — one place for the service lifecycle, so an
  Activity recreation cannot start a second server. Reads the version from
  `strings.js`, so the native layer and the front end cannot disagree about it.
- The status card shows the tablet's LAN address, ready to paste into the DE1 plugin.

### Removed

- Client mode: `setServerBase()`, `needsServerConfig`, and the first-run server-address
  screen.
- **The "stop service" button does not appear on Android.** There is no terminal on a
  tablet, and stopping the service is equivalent to killing the app.

### Fixed

- **The packaged build no longer ships a literal `{{VERSION}}` in its page title.**
  Cosmetic on Android, since the WebView does not display the title, but wrong.
- **Template substitution is now limited to files under `web/`.** It previously applied
  to any `.html` / `.js` / `.css`, including the test pages under `tests/` — where it
  rewrote placeholder literals that existed as *assertion content*. One assertion
  holding `'{{VERSION}}'` had its literal replaced with the real version, so it silently
  became "the title must not contain 2.1-beta.1" and could never pass. The file looked
  perfectly normal throughout.

### Design notes

- **`/api/print` takes the complete ESC/POS byte stream, not a bitmap.** The protocol is
  assembled in exactly one place (`web/printer.js`, byte-for-byte with
  `printers/escpos.py`). Reimplementing it in Java would create a second place to drift,
  and the symptom would be "prints sent over the LAN differ from prints made from the
  app".
- **No template substitution in Java** — the front end resolves `{{VERSION}}` and
  `{{LANG}}` itself, verified on both the browser and the APK paths.
- **Desktop-only features return an empty result that explains itself, not a 404.** AI
  translation and online update are not implemented on Android; the front end calls
  those endpoints, and "this endpoint does not exist" and "this build does not have this
  feature" are different things.
- **The WebView still loads from APK assets** and talks to `http://localhost:8000` over
  CORS, rather than pointing Capacitor's `server.url` at the loopback — which keeps the
  working Bluetooth bridge untouched, and leaves the page renderable (reporting that it
  cannot connect) instead of blank when the service is not up.

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
| `tests/apk_sim.html` | 11 | the APK code path (bundled assets, no client mode) |

132 tests in total, all passing.

**Verified on real hardware** — a Samsung SM-X210 running Android 16, on a real LAN:

- the tablet serves port 8000 to the LAN; `http://192.168.1.225:8000/` returned 200
  when requested from a Mac
- every static asset was retrievable, including the 16 MB font
- `POST /upload` succeeded — the shot was stored, parsed into a bean and a profile, and
  queued for printing
- the UI showed the tablet's own LAN address, the data card appeared, and the Canvas
  thumbnail rendered

**Not verified**: no printer and no DE1 were available, so the SPP handshake, the
512-byte chunking with a 20 ms gap, and the whole ESC/POS byte layout remain untested
against paper. The permission flows ran on Android 16 only, not on 12, 13 or 14.

## 2.1-beta.1

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
