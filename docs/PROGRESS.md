# Progress Report

[中文](PROGRESS_zh.md) | English

**Status as of 2026-09-18.** The migration described in `convert.md` is complete at
the software level for macOS and Android. Nothing has been verified against
physical hardware — no printer was available.

---

## What was asked, and what was delivered

| Task (from `convert.md`) | State |
|---|---|
| 1. Canvas drawing in the web UI | **Done** — `web/render.js`, the only renderer |
| 2. A unified print interface | **Done** — `printers/base.py` + `POST /api/print` |
| 3. macOS print adapter | **Done** — `printers/mac_printer.py`, verified through real CUPS |
| 4. Android print adapter | **Done** — Capacitor plugin + `printers/android_printer.py` bridge |
| 5. Server refactor | **Done** — Pillow and all rendering removed |
| 6. Web UI refactor | **Done** — no PNG dependency, canvas throughout |

Also delivered beyond the brief: Linux and Windows adapters, a first-run server
setup screen, a Bluetooth settings screen, and an APK.

---

## Acceptance criteria

### macOS

| Criterion | State |
|---|---|
| Web UI shows canvas thumbnails of history | **Met** — verified in a real browser |
| Clicking a thumbnail shows the large view | **Met** — verified |
| Print reaches the configured 80mm printer | **Met at the byte level** — driven through real CUPS; see below |
| The server generates no more PNG files | **Met** — `/images/*` returns 404, no image directory exists |

The print path was verified end to end without hardware: a virtual CUPS queue
pointing at a local listener, a real job submitted through the adapter, and the
bytes CUPS emitted compared against what went in. Both modes passed:

- `driver` mode: CUPS emitted a valid PBM, payload byte-identical
- `raw` mode: CUPS emitted `ESC @` + `GS v 0` + payload + cut, byte-identical

This is the strongest verification available without a printer. It proves the CUPS
invocation, the flags, the file format and the byte layout are all correct. It does
**not** prove the printer will render them properly.

### Android

| Criterion | State |
|---|---|
| Web UI runs in an Android WebView | **Met** — bundled in the APK, verified in-bundle |
| Scan and connect a Bluetooth printer | **Built, not exercised** — no hardware |
| Print over Bluetooth ESC/POS | **Built, not exercised** — no hardware |
| Background service with a notification | **Built, not exercised** — needs Doze testing |

### General

| Criterion | State |
|---|---|
| Server startup overhead drops | **Met** — 0.165 s, and the server imports nothing outside the standard library |
| Identical rendering on macOS and Android | **Met by construction** — one renderer, one font, shipped in both |

---

## Verification

133 automated tests, all passing:

| Suite | Tests | Needs |
|---|---|---|
| Front-end JS syntax | 5 files | `node` |
| `tests/test_printers.py` | 18 | nothing |
| `tests/test_platform_dispatch.py` | 20 | nothing |
| `tests/test_server.py` | 22 | nothing |
| `tests/test_cups_e2e.py` | 3 | `lpadmin` rights |
| `tests/web_test.html` | 36 | Chrome + server |
| `tests/ui_test.html` | 22 | Chrome + server |
| `tests/apk_sim.html` | 12 | Chrome + server |

```bash
./tests/run_all.sh
```

---

## Bugs found and fixed during the work

These are worth listing because every one of them was **silent** — none produced an
error, and several produced a plausible-looking result.

| # | Bug | Symptom if shipped |
|---|---|---|
| 1 | `lpstat` output is localised; the parser matched the English word `printer` | On a Chinese macOS **zero printers found**, silently. The user's machine had four, including two 80mm thermal units |
| 2 | `{{LANG}}` is substituted by the server, but APK assets are copied verbatim | `JSON.parse('{{LANG}}')` threw on line 1 of `app.js` — **the whole UI was blank**, with no visible error |
| 3 | The web UI used relative API paths | Inside the APK every request hit the WebView itself instead of the LAN server — **app installed but unusable** |
| 4 | `web/` assets were referenced as `/web/*`, but the APK puts them at the root | Styles and scripts 404 in the packaged app while the browser looked fine |
| 5 | The font lived outside `web/` | Not bundled into the APK — Chinese would fall back to the system font, **breaking cross-platform render parity** |
| 6 | `android.R.drawable.stat_sys_data_sync` is a hidden resource | Java compile error; only surfaced against the real Android SDK |
| 7 | `----` inside an XML comment | Illegal XML; `mergeDebugResources` failed |
| 8 | Serving inherited `SimpleHTTPRequestHandler`'s directory listing | **`settings.json` was readable over HTTP — including the DeepSeek API key.** Inherited from Beta; the service is meant to be LAN-reachable |
| 9 | The export step set the fill colour to white and never switched it back | The mockup print bitmap was **entirely blank at the correct size** |

Bug 8 is a security issue and the most important of the set. Bugs 2–5 would each
have made the Android app useless.

---

## Not verified — the honest list

- **No printer has been touched.** Byte-level correctness is tested; paper-level
  correctness is not. This distinction matters.
- **Bluetooth SPP** — pairing, connection, chunk pacing and reconnection are
  implemented but have never run against a real device.
- **Android 12/13/14 permission flows** — branched by API level, untested on real
  devices.
- **Foreground service across Doze** — untested.
- **Windows** — the spooler calls (`OpenPrinterW`, `StartDocPrinterW`, `WritePrinter`)
  cannot be compiled or run on macOS. The payload construction is tested; the API
  calls are not.
- **Linux** — the CUPS path is shared with macOS and was exercised on macOS, but
  distribution and CUPS-version differences still need a real Linux box.
- **Printed output fidelity** — whether the chart is legible at 203dpi on real
  thermal paper is unknown.

---

## Next steps

1. **Test with a real 80mm printer** — macOS first. `--list-printers`, then press
   print on a card. This is the single highest-value next step.
2. **Check legibility.** The chart is dense at 576 dots. Font sizes may need to
   grow once there is real paper to look at.
3. **Android on a real device** — pair a printer, run the test page, then confirm
   the foreground service survives being backgrounded.
4. **Windows/Linux** — a build on each platform, then a real print.
5. **Consider a print preview.** Now that rendering is client-side this is nearly
   free, and it would make paper-size problems obvious before wasting paper.

## Known rough edges

- The APK is a **debug** build — unsigned for release, and it always trusts the
  debug keystore. A release build needs a signing config.
- The app name lives in three places (`capacitor.config.json`, `strings.xml`, the
  manifest label). The `strings.xml` overlay makes it deterministic, but the three
  can still drift.
- `printers/android_printer.py` (the server-side bridge to a local Android app) has
  never been exercised end to end; it only applies when the Python service itself
  runs on Android, which is not the normal deployment.
