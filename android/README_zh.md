# PrintTheShotNext Android 端(Capacitor + 原生蓝牙打印)

中文 | [English](README.md)

> ⚠️ **测试状态**:这份 Android 端代码在代码层面是完整的,通过了 Java 编译级检查
> (Java 8 与 Java 17 两个目标)以及 ESC/POS 常量的单元冒烟测试,但**从未在真实
> 热敏打印机上跑过** —— 写它的时候手边没有硬件。所有「不需要打印机就能验」的部分
> _(能不能编译、指令字节对不对、权限流程有没有挂到正确的 API 等级上)_ 都验过了;
> 所有需要打印机的部分列在[哪些没验证](#10-哪些没验证)里。

一个 Capacitor 外壳:把项目现成的 Web UI(`../web`)放进 Android WebView,通过原生
ESC/POS 插件打印到**蓝牙热敏小票机**,手机上不需要再跑 Python 服务端。

### 为什么没有用厂商 SDK(刻意决定,不是遗漏)

本项目**刻意不依赖任何打印机厂商的 SDK**,蓝牙连接、ESC/POS 指令拼装全部自己实现。
这是个有意的取舍,不是漏做 —— 所以写在这里,免得日后有人「补上」它:

**这么选的理由**

- **协议只有一份实现。** ESC/POS 的字节在 `web/printer.js` 里拼好,Mac/Windows/Linux
  走 HTTP 送到 `printers/`,Android 走蓝牙原生插件。三方用的是同一份代码。如果
  Android 换成厂商 SDK 自己拼指令,协议就有了两份实现,迟早会走偏 —— 而症状是
  「只有 Android 打出来不对」,在桌面上完全看不见。
- **不挑品牌。** 任何兼容 ESC/POS 的热敏机都能用,不绑定某一家。
- **仓库里没有二进制包。** 厂商 SDK 是一个 `.aar` 二进制,放进公开仓库涉及再分发
  授权问题;自己实现则没有这个顾虑。

**代价(如实说明)**

- 不支持 CPCL / TSPL / ZPL 等**标签机**协议,只支持小票机的 ESC/POS。
- 拿不到打印机状态回传(缺纸、开盖、电量),只能知道「字节发出去了」。
- 各机型对 ESC/POS 的实现差异要自己兼容(比如部分便携机不接受切纸指令,
  所以 `cut` 是可配置的)。

如果日后确实需要标签机或状态回传,再引入厂商 SDK 也不冲突:插件的
`printRaw({data, address})` 接口不用变,换掉的是 `BluetoothPrinter` 内部实现。

---

## 1. 整体结构

```
web/index.html + app.js + render.js     现成的 Web UI(和桌面端共用同一份)
        │  base64 的 ESC/POS 字节流
        ▼
Capacitor.Plugins.PrintTheShotPrinter   原生插件      ─┐
BluetoothPrinterBridge(pyjnius)         静态入口      ─┼─► BluetoothPrinter(同一条 socket)
LocalPrintBridge(127.0.0.1:9100)        本机 HTTP 桥  ─┘
        │  经典蓝牙 SPP
        ▼
热敏小票机(ESC/POS)
```

Web UI 负责拼出**完整**的字节流 —— 复位 → `GS v 0` 光栅位图 → 走纸 → 切纸 —— 原生层
把它原样写进蓝牙 socket,一个字节都不解析、不追加:字节怎么组装只由一个地方决定。
(Python 服务端走 `../printers/escpos.py`,同样是先拼好整份再交给桥接。)

有三条入口是因为项目有三种部署形态:

| 调用方 | 入口 | 适用场景 |
|---|---|---|
| WebView 里的 JS | `PrintTheShotPrinterPlugin` | 正常的 Android 部署方式 |
| 同进程里的 Python | `BluetoothPrinterBridge`(静态) | Python 嵌在 App 内,通过 pyjnius 调 Java |
| 本机 Python 服务端 | `LocalPrintBridge`(HTTP 回环) | 服务端就跑在这台 Android 上(`../printers/android_printer.py`) |

三者共用同一个 `BluetoothPrinter` 单例,也就是同一条蓝牙连接 —— 几乎所有热敏机同时
只接受一条 SPP 连接。

## 2. 目录结构

```
android/
├── capacitor.config.json              Capacitor 配置(webDir 指向 ../web)
├── package.json                       只依赖 3 个 Capacitor 包,没有别的
├── README.md / README_zh.md           本文档
└── app/src/main/                      ← 叠加到生成工程里的内容
    ├── AndroidManifest.xml            权限 + 前台服务 + 明文流量
    ├── res/xml/
    │   ├── network_security_config.xml  允许访问局域网明文 HTTP 服务
    │   └── file_paths.xml               FileProvider 路径(Capacitor 默认)
    └── java/com/printtheshot/
        ├── app/MainActivity.java        注册插件
        └── printer/
            ├── PrintTheShotPrinterPlugin.java  Capacitor 插件(JS 接口)
            ├── BluetoothPrinterBridge.java     给 pyjnius 的静态入口
            ├── BluetoothPrinter.java           SPP 连接管理器(单例)
            ├── PrinterService.java             前台服务(后台保活)
            ├── LocalPrintBridge.java           本机 HTTP 桥
            └── EscPos.java                     ESC/POS 常量与拼装
```

`app/src/main/` 是**叠加层**,不是一个能独立构建的 Gradle 工程:Gradle 文件、主题、
图标、`strings.xml` 都来自 `npx cap add android` 生成的工程。见下一节。

## 3. 构建

### 环境要求

| 工具 | 版本 | 说明 |
|---|---|---|
| Node.js | 18+ | Capacitor 6 要求 |
| JDK | 17 | Capacitor 6 用 Java 17 |
| Android SDK | API 34(`compileSdk`/`targetSdk`) | 用 Android Studio 装最省事 |
| Android Studio | 较新版本 | 不是必须,但装 SDK 最方便 |

> Capacitor 7 也能用:它要 JDK 21 + `compileSdk` 35。本目录的 Java 源码没有用到
> Java 8 以上的任何特性,所以两个版本都能原样编译。

### 步骤

```bash
# 1. Capacitor 工程根目录就是本目录(android/)
cd android
npm install

# 2. 生成原生 Android 工程。
#    Capacitor 用平台名给生成目录命名,所以在这个布局下它落在 android/android/,
#    这是预期行为,原因见下面的说明。
npx cap add android

# 3. 把本目录的叠加层覆盖上去
cp -R app/src/main/java/com/printtheshot  android/app/src/main/java/
cp    app/src/main/AndroidManifest.xml    android/app/src/main/AndroidManifest.xml
cp    app/src/main/res/xml/*.xml          android/app/src/main/res/xml/

# 4. 把 Web UI 拷进 App,并刷新插件列表
npx cap sync android

# 5. 用 Android Studio 打开运行,或者命令行构建
npx cap open android
#   或:cd android && ./gradlew assembleDebug
```

`../web` 改了之后要重跑第 4 步(`npx cap sync android`)才会生效;Java 源码改了要
重跑第 3 步,或者直接改 `android/android/` 下的那份。

> **为什么是 `android/android/`?** 这里的 Capacitor 工程根目录是 `android/`
> (`capacitor.config.json` 在这,`webDir` 指向 `../web`),而 Capacitor 总是在工程根
> 目录下用平台名建目录。如果你更习惯 Capacitor 的常见布局 —— 配置放仓库根目录、
> `android/` 本身就是生成出来的工程 —— 把 `package.json` 和 `capacitor.config.json`
> 往上挪一层,在那里跑 `npx cap add android`,再覆盖同一批 `app/src/main` 文件即可,
> Java 源码两边通用。

### 发布构建

```bash
cd android/android
./gradlew assembleRelease      # 未签名;签名在 Android Studio 里配
```

## 4. 配置

`capacitor.config.json`:

| 配置项 | 值 | 为什么 |
|---|---|---|
| `appId` | `com.printtheshot.app` | 应用 ID,也是 Java 包名根 |
| `appName` | `PrintTheShotNext` | 桌面图标显示的名字 |
| `webDir` | `../web` | 直接复用仓库里现成的 Web UI,不用维护第二份副本 |
| `server.androidScheme` | `http` | 见下 |
| `android.allowMixedContent` | `true` | 页面要调局域网明文 HTTP 服务 |

**关于 `androidScheme: "http"`。** Capacitor 默认是 `https`,WebView 的来源就是
`https://localhost`,此时请求 `http://192.168.x.x:8000` 属于混合内容,Chromium 默认
拦掉。而本 App 的整个前提就是要访问局域网里的明文 HTTP 服务,所以把来源设成 `http`,
这些请求就是普通的同协议请求,不需要额外开口子。`http://localhost` 本身仍然是安全
上下文(secure context),依赖安全上下文的浏览器 API 照样可用。将来如果某个插件
坚持要 `https` 来源,改成 `"https"` 并依赖 `allowMixedContent: true` 即可 —— 两种都
配好了,`http` 只是少几个环节。

**明文流量。** `AndroidManifest.xml` 里同时写了
`android:usesCleartextTraffic="true"` 和
`android:networkSecurityConfig="@xml/network_security_config"`,后者也允许明文。
API 24 起以网络安全配置为准,两边都写成允许,行为才不会打架。以后想收紧:把
`base-config` 改成 `cleartextTrafficPermitted="false"`,再加一段 `domain-config`
只放行你自己的服务端地址。

## 5. 蓝牙权限

| 权限 | 适用版本 | 干什么用 | 用户拒绝的后果 |
|---|---|---|---|
| `BLUETOOTH`、`BLUETOOTH_ADMIN` | API ≤ 30(`maxSdkVersion="30"`) | 这些版本上是安装时权限 | 不适用,装完就有 |
| `BLUETOOTH_CONNECT` | API 31+ | 列已配对设备、建立 SPP 连接 | `listPrinters()` 返回空列表;`connect()` 报缺权限 |
| `BLUETOOTH_SCAN` | API 31+ | 连接前取消正在进行的扫描 | 仍能连上,只是有扫描在跑时会变慢 |
| `POST_NOTIFICATIONS` | API 33+ | 前台服务的常驻通知 | 服务照跑,通知被藏起来,不影响打印 |
| `FOREGROUND_SERVICE`、`FOREGROUND_SERVICE_DATA_SYNC` | API 28+ / 34+ | 保活服务 | 服务起不来,前台打印照常 |
| `INTERNET` | 全部 | WebView 资源 + 访问局域网服务 | 什么都跑不起来 |

第一次调蓝牙接口之前,从 JS 里申请:

```js
const { PrintTheShotPrinter } = Capacitor.Plugins;
const { granted } = await PrintTheShotPrinter.requestPermissions();
```

`requestPermissions()` 在 Android 12+ 上申请 `BLUETOOTH_CONNECT` 和
`BLUETOOTH_SCAN`,在 Android 13+ 上再加一个 `POST_NOTIFICATIONS`。Android 11 及以下
的蓝牙权限是安装时就给的,所以直接返回 `{granted: true}`。返回对象是
`{granted: boolean, notifications: boolean}` —— `granted` 表示蓝牙权限(接口约定),
`notifications` 单独报告通知权限,因为拒绝通知**绝不能**影响打印。

不需要定位权限:本 App 只用**已配对**的设备,从不扫描,这正是 `BLUETOOTH_SCAN` 上
`usesPermissionFlags="neverForLocation"` 所声明的前提。

## 6. 支持的打印机

- **经典蓝牙 SPP**(串口协议,UUID `00001101-0000-1000-8000-00805F9B34FB`)——
  热敏小票机的标准通道。
- **ESC/POS** 指令集,58mm(384 点)或 80mm(576 点)宽。光栅宽度由 Web UI 决定,
  原生层不关心。
- 打印机必须**先在系统设置里配对**。本 App 只列已配对设备、刻意不做扫描:经典 SPP
  小票机本来就得先配对,而扫一遍要多申请权限、多等十几秒,没有收益。在 Android 的
  蓝牙设置里配对一次,然后回到 App 刷新列表。
- **不支持**:只走 BLE(低功耗蓝牙)、不暴露 SPP 通道的打印机,以及 USB / 网络
  打印机。BLE 要的是另一套传输(GATT 特征、MTU 协商),不在本插件范围内。

连接时会先试不安全 RFCOMM、失败再试安全 RFCOMM(不少小票机做不了安全模式的链路
协商),哪条通了就这一整个会话都用它。

## 7. JS 接口

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

- 方法名与上面完全一致 —— 这是与 `web/printer.js` 的接口约定。
- `printRaw` 收到的 `data` 是**完整**的 ESC/POS 字节流(base64),含复位、`GS v 0`
  位图、走纸、切纸。原生层原样写出,不解析、不补拼。
- `printRaw` 的 `address` 可以省略或传空:此时复用当前连接,或者上次连接成功的那台
  (记在本机 `SharedPreferences` 里)。
- **错误处理。** 参数本身有问题(缺 `data`、base64 不合法)会 **reject**,JS 侧进
  `catch`;而**打印失败**(没连接、打印机没开、写到一半断线)是 resolve 成
  `{success: false, message}` —— 「没打成」是一个正常结果,不是调用错误。请务必判断
  `success`。
- 所有蓝牙操作都在后台线程,不会卡住界面。

### 静态入口(pyjnius)

```java
BluetoothPrinterBridge.attach(context);                                  // 启动时调一次
boolean ok = BluetoothPrinterBridge.printRaw(base64Data, "AA:BB:CC:DD:EE:FF");
String why = BluetoothPrinterBridge.getLastError();
```

`printRaw` 是**阻塞**的,这样才有布尔返回值可用 —— 绝对不要在 UI 线程上调(在 Python
里就是别在主线程调)。`attach()` 由 `MainActivity`、插件的 `load()` 和服务的
`onCreate()` 自动调用,正常情况下不用管。

### 本机 HTTP 桥

`startForegroundService()` 会顺带在 `127.0.0.1:9100` 起一个小 HTTP 服务(用
`PrinterService.start(context, false)` 可以关掉),这正是
`../printers/android_printer.py` 探测的那个桥:

| 方法 | 路径 | 请求体 | 响应 |
|---|---|---|---|
| GET | `/ping` | — | `200 {"ok":true, ...}` |
| GET | `/printers` | — | `{"printers":[{address,name,paired,default,status}]}` |
| POST | `/print` | `{"data":"<base64>","address":"AA:BB:.."}` | `{"success":bool,"message":str,"printer":str}` |

它只绑回环地址,局域网里别的机器连不上 —— 但注意它**没有鉴权**,而设备上任何 App
都能连回环端口。风险是「同一台设备上的恶意 App 可以打一张小票」,对专用打印 App
来说可以接受;起服务时把桥关掉就能完全消除。同一时刻只处理一个连接,这样两份任务
不会交叉写进同一条蓝牙连接。

## 8. 前台服务(后台保活)

需要「App 切到后台也别断连」时起它,不用了就停。前台服务一直挂着费电,而且从
**Android 15(API 35)起,dataSync 类型有每日累计时长上限**,大约六小时后会被系统
掐掉。请把它当成「要用才开」的东西,而不是常驻。

服务声明了 `android:foregroundServiceType="dataSync"`(打印属于在本机与外部设备之间
搬运数据)—— Android 14(API 34)起必须要,不声明类型会抛
`MissingForegroundServiceTypeException`。它是 `exported="false"`,App 之外起不了它。
拒绝 `POST_NOTIFICATIONS` 只会让通知被藏起来,不影响服务运行。

## 9. 故障排查

| 现象 | 原因 / 处理 |
|---|---|
| `Capacitor.Plugins.PrintTheShotPrinter` 是 `undefined`,还不报错 | 插件没注册。`MainActivity` 必须在 `super.onCreate()` **之前**调 `registerPlugin(PrintTheShotPrinterPlugin.class)`。这是最容易踩的坑 |
| 打印机列表总是空 | 没有已配对设备,或者 `BLUETOOTH_CONNECT` 一直没授权。先在 Android 蓝牙设置里配对,再调 `requestPermissions()` 重试。可看 `adb logcat -s PrintTheShotNext` |
| `connect()` 报「连接超时」 | 打印机没开、不在范围内,或者已经被别的设备连着(手机/平板常占着 SPP 连接)。给打印机断电重启;确认没有别的 App 连着 |
| 连上了但打不出东西 | 多数是打印机侧**缓冲区溢出**,或者数据不对。原生层因此每 512 字节写一次、块间停 20ms —— 你的机器还需要更慢的话,调大 `BluetoothPrinter.java` 里的 `CHUNK_GAP_MS` |
| 打到一半停住 | 链路中途断了。这里**刻意不自动重打**:重发会吐出半张重复的小票。在小票界面重新点一次打印 |
| 打出乱码 | base64 内容不是合法的 ESC/POS 流、光栅宽度和机器不匹配(80mm 是 576 点,58mm 是 384 点),或者掺了文本代码页。整张图都用位图输出 —— Web UI 就是这么做的,可以完全绕开代码页 |
| 不切纸 | 很多便携机没有切刀。在 Web UI 里关掉切纸(`do_cut`),或者接受只走纸 |
| `CLEARTEXT communication not permitted` | 网络安全配置没进构建。确认 `res/xml/network_security_config.xml` 拷进去了,且 manifest 引用了它 |
| 打开一片白 | Web 资源没同步:跑 `npx cap sync android`。确认 `webDir` 仍指向 `../web` |
| Android 14 上 `startForeground` 抛异常 | 要么 manifest 少了 `FOREGROUND_SERVICE_DATA_SYNC`,要么 `<service>` 上没写 `foregroundServiceType="dataSync"` |
| 构建报 `@style/AppTheme.NoActionBarLaunch` 找不到 | 叠加层被拷到了不是生成工程的目录。先用 `npx cap add android` 生成,再拷叠加层 |

日志过滤:`adb logcat -s PrintTheShotNext`。

## 10. 哪些没验证

手边没有打印机,所以说清楚:

- **从未在真实打印机上跑过。** SPP 握手、512 字节分块 + 20ms 间隔、以及「打印机
  能接受不安全 RFCOMM」这个假设,都需要真机确认。
- **从未在真实 Android 设备上跑过。** 权限流程按 API 等级做了正确的分支,用的也都是
  Capacitor 和 Android 的公开文档接口,但没有在 Android 12 / 13 / 14 上实测过。
- **编译状态**:Java 源码在 Java 8 和 Java 17 两个目标下都能干净编译(`-Xlint:all`
  无警告),ESC/POS 常量有一份单元冒烟测试覆盖。其余部分是静态审查。
- **叠加是手工步骤。** 没有脚本保证 `app/src/main/` 和生成工程同步;改完要重新拷
  (或者直接改生成工程里那份)。

## 11. 参考与致谢

ESC/POS 指令集和经典蓝牙 SPP 的做法,依据的是公开的 ESC/POS 文档以及第三方打印 SDK
的公开接口文档(Android 打印 SDK 参考实现)—— 指令字节、SPP UUID 和权限模型都是
公开、标准的做法。没有捆绑任何第三方库:插件只用了 Android 平台 API 和 Capacitor
本身。

## 12. 许可证

GPLv3,与项目其余部分一致。
