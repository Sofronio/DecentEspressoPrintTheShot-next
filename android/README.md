# PrintTheShotNext for Android (Capacitor + native Bluetooth printing)

[中文文档](README_zh.md) | English

> ⚠️ **Test status**: this Android shell is complete at the code level and passes
> Java compile-level checks (Java 8 and Java 17 targets) plus a unit smoke test of
> the ESC/POS constants — but it has **never been run against a real thermal
> printer**. There was no hardware available while writing it. Everything that can
> be verified without a printer _(does it compile, are the command bytes right, are
> the permission flows wired to the correct API levels)_ has been verified;
> everything that needs a printer is listed under
> [What is not verified](#10-what-is-not-verified).

A Capacitor wrapper that runs the project's existing web UI (`../web`) inside an
Android WebView and prints to a **Bluetooth thermal receipt printer** through a
native ESC/POS plugin, with no Python service required on the phone.

### Why no vendor SDK (a deliberate decision, not an oversight)

This project **deliberately depends on no printer vendor's SDK**. The Bluetooth
connection and the ESC/POS assembly are implemented here, from scratch. It is a
trade-off rather than a gap, so it is written down — otherwise someone will
helpfully "fix" it later:

**Why**

- **One implementation of the protocol.** The ESC/POS bytes are assembled in
  `web/printer.js`; macOS/Windows/Linux deliver them over HTTP to `printers/`, and
  Android delivers them over Bluetooth through the native plugin. All three use that
  same code. Hand the Android side a vendor SDK that assembles its own commands and
  the protocol now exists twice — and the symptom of the inevitable drift is
  "Android prints wrong, and only Android", which is invisible from the desktop.
- **No brand lock-in.** Any ESC/POS-compatible thermal printer works.
- **No binary blob in the repository.** A vendor SDK is an `.aar` binary, and
  committing one to a public repository raises redistribution-licence questions.
  A self-contained implementation has no such problem.

**What it costs (stated plainly)**

- No CPCL / TSPL / ZPL — receipt printers only, not label printers.
- No printer status feedback (paper out, cover open, battery). All you learn is that
  the bytes were sent.
- Device-specific ESC/POS differences are ours to handle. Some portable units reject
  the cut command, which is why `cut` is configurable.

If label printers or status feedback are ever needed, adding a vendor SDK later does
not conflict with this: the plugin's `printRaw({data, address})` interface stays as
it is, and only the internals of `BluetoothPrinter` change.

---

## 1. How it fits together

```
        ┌─────────────────────────── the tablet IS the server ───────────────────────┐
        │                                                                            │
DE1 ──► │  :8000  MiniHttpServer (Java, hand-rolled, no dependencies)                │
        │           ├── serves the web UI out of the APK assets                      │
        │           ├── POST /upload      → ShotStore (app-private storage)          │
        │           └── /api/*            → history, statistics, print queue         │
        │                    ▲                                                       │
        │                    │ CORS                                                 │
        │  Capacitor WebView │  web/index.html + app.js + render.js                  │
        │                    │  polls the print queue, renders the chart on canvas   │
        │                    ▼                                                       │
        │                base64 ESC/POS byte stream                                  │
        │                    ▼                                                       │
        │  Capacitor.Plugins.PrintTheShotPrinter ─┐                                  │
        │  BluetoothPrinterBridge (pyjnius)       ─┼─► BluetoothPrinter (one socket)  │
        │  LocalPrintBridge (127.0.0.1:9100)      ─┘                                 │
        │                    │ classic Bluetooth SPP                                 │
        │                    ▼                                                       │
        │             thermal receipt printer (ESC/POS)                              │
        └────────────────────────────────────────────────────────────────────────────┘
```

The DE1 uploads to the tablet directly; nothing else is needed.

The web UI builds the **complete** byte stream — reset → `GS v 0` raster bitmap →
feed → cut — and the native layer writes it into the Bluetooth socket verbatim.
The native side never parses or appends a single byte: how the bytes are composed
is decided in exactly one place. (The Python service builds its own complete job
the same way via `../printers/escpos.py`.)

Three entry points exist because the project has several deployment shapes:

| Caller | Entry point | When it applies |
|---|---|---|
| JS inside the WebView | `PrintTheShotPrinterPlugin` | The normal Android deployment |
| Python in the same process | `BluetoothPrinterBridge` (static) | Python embedded in the app, calling Java through pyjnius |
| Local Python service | `LocalPrintBridge` (HTTP, loopback) | The service runs on this same Android device (`../printers/android_printer.py`) |

All three share one `BluetoothPrinter` singleton, i.e. one Bluetooth connection —
almost every thermal printer accepts only a single SPP link at a time.

## 2. Directory layout

```
android/
├── capacitor.config.json              Capacitor config (webDir → ../web)
├── package.json                       Capacitor deps (3 packages, nothing else)
├── README.md / README_zh.md           this document
└── app/src/main/                      ← the overlay copied into the generated project
    ├── AndroidManifest.xml            permissions + foreground service + cleartext
    ├── res/xml/
    │   ├── network_security_config.xml  cleartext HTTP for the LAN service
    │   └── file_paths.xml               FileProvider paths (Capacitor default)
    └── java/com/printtheshot/
        ├── app/MainActivity.java        registers the plugin
        └── printer/
            ├── PrintTheShotPrinterPlugin.java  Capacitor plugin (the JS API)
            ├── BluetoothPrinterBridge.java     static entry for pyjnius
            ├── BluetoothPrinter.java           SPP connection manager (singleton)
            ├── PrinterService.java             foreground service (keep-alive)
            ├── LocalPrintBridge.java           loopback HTTP bridge
            └── EscPos.java                     ESC/POS constants + helpers
```

`app/src/main/` is an **overlay**, not a standalone Gradle project: the Gradle
files, theme, icons and `strings.xml` come from the project that
`npx cap add android` generates. See the next section.

## 3. Building

### Requirements

| Tool | Version | Notes |
|---|---|---|
| Node.js | 18+ | Capacitor 6 requires it |
| JDK | 17 | Capacitor 6 targets Java 17 |
| Android SDK | API 34 (`compileSdk`/`targetSdk`) | Install via Android Studio |
| Android Studio | recent | Not strictly required, but the easiest way to get the SDK |

> Capacitor 7 also works: it needs JDK 21 and `compileSdk` 35. None of the Java
> sources in this directory use anything newer than Java 8, so both versions
> compile unchanged.

### Steps

```bash
# 1. Capacitor project root is this directory (android/)
cd android
npm install

# 2. Generate the native Android project.
#    Capacitor names the generated directory after the platform, so with this
#    layout it lands in android/android/ — that is expected, see the note below.
npx cap add android

# 3. Copy this overlay onto the generated project.
cp -R app/src/main/java/com/printtheshot  android/app/src/main/java/
cp    app/src/main/AndroidManifest.xml    android/app/src/main/AndroidManifest.xml
cp    app/src/main/res/xml/*.xml          android/app/src/main/res/xml/

# 4. Copy the web UI into the app and update the plugin list.
npx cap sync android

# 5. Open in Android Studio and run, or build from the command line.
npx cap open android
#   or: cd android && ./gradlew assembleDebug
```

After any change to `../web`, run `npx cap sync android` again (step 4) to copy
the new assets. After any change to the Java sources, re-run step 3 or edit the
files under `android/android/` directly.

> **Why `android/android/`?** The Capacitor project root here is `android/`
> (that is where `capacitor.config.json` lives, pointing at `../web`), and
> Capacitor always creates the platform directory under the project root using
> the platform name. If you would rather use Capacitor's usual layout — config at
> the repository root, `android/` itself being the generated project — move
> `package.json` and `capacitor.config.json` up one level, run
> `npx cap add android` there, and overlay the same `app/src/main` files; the
> Java sources are identical either way.

### Release build

```bash
cd android/android
./gradlew assembleRelease      # unsigned; configure signing in Android Studio
```

## 4. Configuration

`capacitor.config.json`:

| Setting | Value | Why |
|---|---|---|
| `appId` | `com.printtheshot.app` | Application ID and Java package root |
| `appName` | `PrintTheShotNext` | Launcher label |
| `webDir` | `../web` | Reuses the repository's existing web UI — no second copy to keep in sync |
| `server.androidScheme` | `http` | See below |
| `android.allowMixedContent` | `true` | The page calls a plain-HTTP LAN service |

**On `androidScheme: "http"`.** Capacitor's default is `https`, which makes the
WebView origin `https://localhost`; requests to `http://192.168.x.x:8000` are then
mixed content and are blocked by Chromium unless you opt out. Since this app is
built entirely around talking to a plain-HTTP LAN service, the origin is set to
`http` so the requests are ordinary same-scheme requests rather than something
that needs an override. `http://localhost` is still a secure context, so
secure-context APIs keep working. If a future plugin insists on an `https`
origin, switch to `"https"` and rely on `allowMixedContent: true` — both are
configured to work, the `http` origin just has fewer moving parts.

**Cleartext HTTP.** `AndroidManifest.xml` sets both
`android:usesCleartextTraffic="true"` and
`android:networkSecurityConfig="@xml/network_security_config"`, and the config
permits cleartext. From API 24 the network security config takes precedence, so
both are set to allow and the behaviour cannot diverge. To tighten it later, set
`base-config` to `cleartextTrafficPermitted="false"` and add a `domain-config`
for your server's host only.

## 5. Bluetooth permissions

| Permission | Applies to | Why | If the user declines |
|---|---|---|---|
| `BLUETOOTH`, `BLUETOOTH_ADMIN` | API ≤ 30 (`maxSdkVersion="30"`) | Install-time permissions on those versions | N/A — granted at install |
| `BLUETOOTH_CONNECT` | API 31+ | Listing bonded devices, opening the SPP socket | `listPrinters()` returns an empty list; `connect()` reports the missing permission |
| `BLUETOOTH_SCAN` | API 31+ | Cancelling an in-flight discovery before connecting | Connection still works, just slower if a discovery is running |
| `POST_NOTIFICATIONS` | API 33+ | The foreground service's persistent notification | The service still runs; the notification is hidden. Printing is unaffected |
| `FOREGROUND_SERVICE`, `FOREGROUND_SERVICE_DATA_SYNC` | API 28+ / 34+ | Keep-alive service | The service cannot start; printing in the foreground still works |
| `INTERNET` | all | WebView assets and the LAN HTTP service | Nothing works |

Request them from JS before the first Bluetooth call:

```js
const { PrintTheShotPrinter } = Capacitor.Plugins;
const { granted } = await PrintTheShotPrinter.requestPermissions();
```

`requestPermissions()` asks for `BLUETOOTH_CONNECT` and `BLUETOOTH_SCAN` on
Android 12+ and adds `POST_NOTIFICATIONS` on Android 13+. On Android 11 and below
the Bluetooth permissions are install-time, so it resolves immediately with
`{granted: true}`. The resolved object is
`{granted: boolean, notifications: boolean}` — `granted` covers the Bluetooth
permissions (the contract), `notifications` reports notification consent
separately, because declining notifications must never block printing.

No location permission is required: the app only uses **bonded** devices and
never scans, which is what `usesPermissionFlags="neverForLocation"` on
`BLUETOOTH_SCAN` asserts.

## 6. Supported printers

- **Classic Bluetooth SPP** (Serial Port Profile, UUID
  `00001101-0000-1000-8000-00805F9B34FB`) — the standard channel for thermal
  receipt printers.
- **ESC/POS** command set, 58 mm (384 dots) or 80 mm (576 dots) width. The web UI
  decides the raster width; the native layer does not care.
- The printer must be **paired in system settings first**. The app lists bonded
  devices only and deliberately does not scan: classic SPP printers effectively
  need bonding anyway, and a discovery sweep would cost extra permissions and ten
  seconds for no benefit. Pair it once in Android's Bluetooth settings, then
  reopen the printer list.
- **Not supported**: BLE-only (Bluetooth Low Energy) printers that do not expose
  the SPP profile, and USB/network printers. BLE would need a completely
  different transport (GATT characteristics, MTU negotiation) and is out of scope
  here.

Both the insecure-RFCOMM and secure-RFCOMM variants are attempted (insecure
first, since a fair number of receipt printers cannot complete secure-mode link
negotiation); whichever succeeds is reused for the session.

## 7. JS API

```js
const { PrintTheShotPrinter } = Capacitor.Plugins;

await PrintTheShotPrinter.requestPermissions();                        // {granted, notifications}
const { printers } = await PrintTheShotPrinter.listPrinters();         // {printers: [{address, name, paired, default}]}
await PrintTheShotPrinter.connect({ address });                        // {success, message}
await PrintTheShotPrinter.printRaw({ data: base64String, address });   // {success, message}
await PrintTheShotPrinter.isConnected();                               // {connected, address}
await PrintTheShotPrinter.disconnect();                                // {success}
await PrintTheShotPrinter.startForegroundService();                    // {success, running}
await PrintTheShotPrinter.stopForegroundService();                     // {success, running}
```

- Methods are named exactly as above — they are the interface contract with
  `web/printer.js`.
- `printRaw` takes the **complete** ESC/POS stream, base64-encoded, including
  reset, the `GS v 0` bitmap, the feed and the cut. The native layer writes it
  verbatim.
- `address` may be omitted or empty in `printRaw`: the current connection is then
  reused, or the last printer that connected successfully (remembered in local
  `SharedPreferences`).
- **Errors.** A malformed argument (missing `data`, invalid base64) **rejects**,
  so the JS `catch` fires. A *print failure* (nothing connected, printer off,
  link dropped mid-job) **resolves with `{success: false, message}`** — "the print
  did not happen" is a legitimate outcome, not a call error. Always check
  `success`.
- All Bluetooth work happens on a background thread; no call blocks the UI.

### The static entry point (pyjnius)

```java
BluetoothPrinterBridge.attach(context);                                  // once, at startup
boolean ok = BluetoothPrinterBridge.printRaw(base64Data, "AA:BB:CC:DD:EE:FF");
String why = BluetoothPrinterBridge.getLastError();
```

`printRaw` **blocks** until the job is done, which is what makes a boolean return
value possible — never call it on the UI thread (in Python: not on the main
thread). `attach()` is called automatically by `MainActivity`, the plugin's
`load()` and the service's `onCreate()`, so normally you do not have to.

### The loopback HTTP bridge

`startForegroundService()` also starts a small HTTP server on `127.0.0.1:9100`
(pass `httpBridge: false` through `PrinterService.start(context, false)` to skip
it), which matches what `../printers/android_printer.py` probes for:

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/ping` | — | `200 {"ok":true, ...}` |
| GET | `/printers` | — | `{"printers":[{address,name,paired,default,status}]}` |
| POST | `/print` | `{"data":"<base64>","address":"AA:BB:.."}` | `{"success":bool,"message":str,"printer":str}` |

It binds to the loopback interface only, so nothing on the LAN can reach it — but
note it has **no authentication**, and any app on the device can connect to a
loopback port. The exposure is "a malicious app on this device could print a
receipt", which is acceptable for a single-purpose printing app; start the
service with the bridge disabled to remove it entirely. One connection is handled
at a time, which is what keeps two jobs from interleaving on the same Bluetooth
link.

## 8. Foreground service (keep-alive)

Start it when the app should survive being backgrounded with the Bluetooth link
intact; stop it when you are done. A permanently running foreground service
drains the battery, and from **Android 15 (API 35) the `dataSync` type has a daily
cumulative runtime cap** — after roughly six hours the system stops it. Treat it
as "on when needed", not as a permanent fixture.

The service declares `android:foregroundServiceType="dataSync"` (printing is
moving data between the device and an external device) — required from Android 14
(API 34), where a foreground service that declares no type throws
`MissingForegroundServiceTypeException`. It is `exported="false"`: nothing outside
the app can start it. `POST_NOTIFICATIONS` denial only hides the notification.

## 9. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `Capacitor.Plugins.PrintTheShotPrinter` is `undefined`, no error | The plugin was not registered. `MainActivity` must call `registerPlugin(PrintTheShotPrinterPlugin.class)` **before** `super.onCreate()`. This is the easiest trap here |
| Printer list is always empty | No bonded devices, or `BLUETOOTH_CONNECT` was never granted. Pair the printer in Android's Bluetooth settings first, then call `requestPermissions()` and retry. Check `adb logcat -s PrintTheShotNext` |
| `connect()` fails with "connection timed out" | The printer is off, out of range, or already connected to another device (phones and tablets often hold the SPP link). Power-cycle the printer; make sure no other app is connected |
| Connection succeeds but nothing prints | Almost always a **buffer overflow** on the printer side, or a data mismatch. The native layer chunks writes at 512 bytes with a 20 ms gap for exactly this reason — if your printer needs more, raise `CHUNK_GAP_MS` in `BluetoothPrinter.java` |
| Printing stops half way through a receipt | The link dropped mid-job. It is deliberately **not** retried: resending would duplicate half a receipt. Print again from the web UI |
| Garbled output | The base64 payload is not a valid ESC/POS stream, the raster width does not match the printer (576 dots for 80 mm, 384 for 58 mm), or a text code page is involved. Render everything as a bitmap — that is what the web UI does, and it sidesteps code pages entirely |
| Cut does not happen | Many portable units have no cutter. Disable the cut in the web UI (`do_cut`), or accept the feed-only ending |
| `CLEARTEXT communication not permitted` | The network security config did not make it into the build. Check `res/xml/network_security_config.xml` was copied, and that the manifest references it |
| The whole page is blank | The web assets were not synced: run `npx cap sync android`. Check `webDir` still points at `../web` |
| `startForeground` throws on Android 14 | Either `FOREGROUND_SERVICE_DATA_SYNC` is missing from the manifest, or `foregroundServiceType="dataSync"` is not on the `<service>` element |
| Build fails on `@style/AppTheme.NoActionBarLaunch` | The overlay was applied to a directory that is not the generated project. Generate with `npx cap add android` first, then copy the overlay in |

Useful log filter: `adb logcat -s PrintTheShotNext`.

## 10. What is not verified

Being explicit, since there was no printer to test with:

- **Never executed on a real printer.** The SPP handshake, the 512-byte chunking
  with a 20 ms gap, and the assumption that printers accept the insecure-RFCOMM
  variant all need hardware confirmation.
- **Never executed on a real Android device.** The permission flows are gated to
  the correct API levels by construction and follow the documented Capacitor and
  Android APIs, but they have not been exercised on Android 12, 13 or 14.
- **Compile status**: the Java sources compile cleanly against Java 8 and Java 17
  targets (with `-Xlint:all`), and the ESC/POS constants are covered by a unit
  smoke test. Everything else is static review.
- **The overlay step is manual.** There is no script keeping `app/src/main/` in
  sync with the generated project; re-copy after editing (or edit the generated
  copy directly).

## 11. Credits and references

The ESC/POS command set and the classic-Bluetooth SPP approach follow the public
ESC/POS documentation and a third-party printing SDK's public interface
documentation (an Android printing SDK reference implementation) — the command
bytes, the SPP UUID and the permission model are the standard, publicly
documented ones. No third-party library is bundled: the plugin uses only the
Android platform APIs and Capacitor itself.

## 12. License

GPLv3, same as the rest of the project.
