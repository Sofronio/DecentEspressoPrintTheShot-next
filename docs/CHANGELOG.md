# Changelog

[中文](CHANGELOG_zh.md) | English

## 1.0.0-beta.4

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

## 1.0.0-beta.3

The release that made the Android app usable as a background print node, and then had to
be reissued because the first attempt shipped broken.

**If you are reading this to decide whether to update: yes.** Two earlier builds under
this same version number were withdrawn — one crashed on launch for anyone installing it
fresh, and the other left the plugin download buttons doing nothing. This is the one that
was tested end to end on real hardware.

### Changed

- **The background service is no longer optional, and its type changed.** Nothing in the
  app ever called `startForegroundService` — it lived in the plugin and in a doc comment
  and nowhere else, so the service could never start and the app was frozen the moment it
  went to the background. It now starts with the app, and the UI has a switch to turn it
  off.
- **`dataSync` → `connectedDevice`.** A Bluetooth printer is an external device, which is
  what `connectedDevice` means; more to the point, from Android 15 `dataSync` carries a
  6-hour-per-24-hour cap and is stopped by the system when it expires, while
  `connectedDevice` is not among the types that limit applies to.

### Fixed

- **Printing stopped in the background.** The pump was a `setInterval` inside the
  WebView, and Chromium throttles a hidden page's timers to roughly once a minute. The
  server now pokes the front end when a shot arrives — an `evaluateJavascript` call, not
  a timer, so the throttling does not apply. Rendering still happens only in the front
  end.
- **The same receipt could print forever.** When a print succeeded but its
  acknowledgement did not arrive, the server kept the job and the next pass claimed it
  again; in practice the printer fed paper continuously. "Print each file once" is now
  this end's own invariant, recorded *before* the ack so a lost ack cannot reprint it.
- **The same shot printed several times.** De-duplication is now by **content**
  (SHA-256, 30 second window) on both the Android and the desktop server, and the front
  end re-fetches the queue per job so the copies the server drops disappear before they
  print.
- **The machine name printed as UNKNOWN while the UI showed de1xl.** The name is not in
  the shot file — that is the uploaded JSON verbatim — it lives in the server's index. It
  now travels on the queue job.
- **Crash on launch after a fresh install.** `connectedDevice` requires not only its
  install-time permission but also at least one **granted** Bluetooth permission, and
  those are runtime permissions — so on the very first run nothing was granted and
  `startForeground` threw every time. What made it fatal was that the service re-threw on
  purpose; the exception escaped `onStartCommand` and took the app with it, before the
  user had seen a screen or had any chance to grant anything. It now fails quietly, logs
  what is missing, and the activity retries on resume.
- **The plugin download buttons did nothing inside the APK.** A WebView does not save
  files and Capacitor sets no download handler; and the Android server was not sending
  `Content-Disposition`, which is why `.txt` was *displayed* rather than saved while
  `.tcl` raised a download event anyway. Handing the URL to the system browser turned out
  to be self-defeating — opening the browser backgrounds the app, and the file has to
  come from the app's own server, which is frozen by then. The buttons now use no
  network: the file ships inside the APK and the native side writes it into Downloads.
- **Upgrading required uninstalling, which wiped your data.** Every build environment
  signed with its own key, and each CI run generated a fresh one, so no build could
  replace another (`INSTALL_FAILED_UPDATE_INCOMPATIBLE`). There is now one committed key
  used by both variants. It is public and protects nothing; the build script says so, and
  says what to do if this ever needs a real release signature.

### Web UI

- The GitHub button opens the file on GitHub rather than the releases page.
- The plugin steps are no longer double-numbered, and now mention creating
  `/de1plus/plugins/print_the_shot/` when it is missing — that folder does not exist on a
  fresh install, so the old wording left the user stuck at step one.
- Step four shows this machine's actual address, with the path beside it.

### Verification

- On a Samsung SM-X210 running Android 16, printing to a **real Bluetooth thermal
  printer**: one upload produced exactly one receipt, the queue drained, and nothing
  printed again afterwards.
- A fresh install with no Bluetooth permission granted: zero crashes, and the keep-alive
  comes up on its own once the permission is granted.
- Both plugin buttons write 19,205 bytes — the size of `plugin/plugin.tcl` — into the
  device's Downloads folder.
- The debug and release APKs carry the same signing certificate.
- The full test suite passes, including four de-duplication tests.

**Not verified**: the shots printed during testing came from a file rather than from a
real DE1, and nothing was printed through the desktop adapters — CUPS, the Windows
spooler and the DE1's own upload path are still untested against paper.

### One more thing

During this release's development the printer fed paper continuously several times, and
it was traced to **the test suite**: `tests/web_test.html` really POSTs `/api/print`, and
its comment — "no printer is attached, that is fine" — only holds on a machine with no
printer configured. Every test run pushed a full chart at the default printer, and a
small-buffer thermal printer cannot absorb that. The tests now start the server with
`PTS_PRINT_DRYRUN=1`. If you run these tests, you get that fix too.

## 1.0.0-beta.2

The release that made the Android app a server in its own right, reversing the client
shape shipped in 1.0.0-beta.1. The tablet now receives shots straight from the DE1,
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

## 1.0.0-beta.1

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
