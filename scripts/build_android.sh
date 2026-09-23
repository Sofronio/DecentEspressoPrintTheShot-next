#!/bin/bash
# PrintTheShot Next — Android 构建脚本 / Android build script
# ===========================================================
#
# 中文
# ----
# 一条命令从仓库构建出 APK。做四件事:
#
#   1. 装 Capacitor 依赖(npm install)
#   2. 生成原生工程(npx cap add android,已存在则跳过)
#   3. 把 web/ 同步进工程,再叠加我们手写的原生源码
#   4. 用 Gradle 打包
#
# 为什么要「叠加」这一步:仓库里手写的是 android/app/src/main/(便于 review、
# 也在版本控制里),而 Capacitor 生成的工程在 android/android/ 下。生成工程
# 不入库(它是产物),所以每次构建都得把手写的部分盖上去。
#
# 依赖 / requirements:
#   - JDK 17
#   - Android SDK(ANDROID_HOME 或 ~/Library/Android/sdk)
#   - node / npm
#
# 用法 / usage:
#   ./scripts/build_android.sh                 # debug APK
#   ./scripts/build_android.sh release         # release APK(需要签名配置)
#
# English
# -------
# Builds an APK from the repository in one command. Four steps:
#
#   1. install Capacitor dependencies (npm install)
#   2. generate the native project (npx cap add android; skipped if present)
#   3. sync web/ into the project, then overlay our hand-written native sources
#   4. package with Gradle
#
# Why the overlay step exists: what the repository tracks is
# android/app/src/main/ (reviewable, version-controlled), while Capacitor
# generates into android/android/. The generated project is not committed — it is
# build output — so the hand-written part has to be laid over it on every build.
#
# Requirements:
#   - JDK 17
#   - Android SDK (ANDROID_HOME, or ~/Library/Android/sdk)
#   - node / npm
#
# Usage:
#   ./scripts/build_android.sh                 # debug APK
#   ./scripts/build_android.sh release         # release APK (needs signing config)

set -euo pipefail
# 目录在开头就全部解析成绝对路径。脚本中途会 cd 好几次,那时候再用 $0 算相对
# 路径就会算到别的地方去 —— 构建成功了、拷贝那一步却找不到地方。
# Resolve everything to absolute paths up front. The script cd's several times, and
# computing a relative path from $0 after that lands somewhere else entirely — the
# build succeeds and the copy step then has nowhere to go.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT/android"             # 到 Capacitor 工程根 / to the Capacitor project root

VARIANT="${1:-debug}"
case "$VARIANT" in
  debug)   GRADLE_TASK="assembleDebug" ;;
  release) GRADLE_TASK="assembleRelease" ;;
  *) echo "❌ 未知的构建类型 / unknown variant: $VARIANT(用 debug 或 release)"; exit 1 ;;
esac

# ---- 环境 / environment ----
if [ -z "${ANDROID_HOME:-}" ] && [ -z "${ANDROID_SDK_ROOT:-}" ]; then
  for candidate in "$HOME/Library/Android/sdk" "$HOME/Android/Sdk" /usr/local/share/android-sdk; do
    if [ -d "$candidate" ]; then
      export ANDROID_HOME="$candidate"
      break
    fi
  done
fi
export ANDROID_HOME="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}"
export ANDROID_SDK_ROOT="$ANDROID_HOME"

if [ -z "$ANDROID_HOME" ] || [ ! -d "$ANDROID_HOME" ]; then
  echo "❌ 找不到 Android SDK / Android SDK not found."
  echo "   装 Android Studio,或设置 ANDROID_HOME。/ install Android Studio, or set ANDROID_HOME."
  exit 1
fi

if [ -z "${JAVA_HOME:-}" ]; then
  JAVA_HOME="$(/usr/libexec/java_home -v 17 2>/dev/null || true)"
fi
if [ -z "$JAVA_HOME" ] || [ ! -d "$JAVA_HOME" ]; then
  echo "❌ 找不到 JDK 17 / JDK 17 not found."
  echo "   Capacitor 6 需要 JDK 17。/ Capacitor 6 requires JDK 17."
  exit 1
fi
export JAVA_HOME

echo "🍳 构建 Android / building Android ($VARIANT)"
echo "   ANDROID_HOME = $ANDROID_HOME"
echo "   JAVA_HOME    = $JAVA_HOME"
echo ""

# ---- 1. 依赖 / dependencies ----
if [ ! -d node_modules ]; then
  echo "▸ 1/4 安装依赖 / installing dependencies"
  npm install --silent
else
  echo "▸ 1/4 依赖已存在 / dependencies present"
fi

# ---- 2. 生成原生工程 / generate the native project ----
if [ ! -d android ]; then
  echo "▸ 2/4 生成原生工程 / generating the native project"
  npx cap add android
else
  echo "▸ 2/4 原生工程已存在 / native project present"
fi

# ---- 3. 同步 web 资源 + 叠加手写源码 / sync assets, overlay our sources ----
echo "▸ 3/4 同步资源与源码 / syncing assets and sources"
npx cap copy android

GEN=android/app/src/main
OURS=app/src/main

# ---- DE1 插件也打进 APK ----
#
# `npx cap copy` 只搬 webDir(web/),而 plugin/plugin.tcl 在仓库根上,于是它
# 从来不进 APK —— 界面上的「下载插件」三个按钮在平板上全 404,而桌面打包版
# (PyInstaller 的 datas 里带了 plugin/)是好的。这类「一个平台好、另一个平台
# 404」的缺口最难发现,因为开发时看的是好的那个。
#
# 放两个名字是刻意的:界面上的 TXT 按钮就是同一个文件换扩展名。蓝牙传 .tcl 常被
# 安卓端拒收,.txt 能过;Python 服务端也是这么做的(见 /plugin/plugin.tcl.txt)。
#
# ---- The DE1 plugin ships in the APK too ----
#
# `npx cap copy` only moves webDir (web/), and plugin/plugin.tcl lives at the
# repository root, so it never made it into the APK — every one of the three plugin
# download buttons 404s on the tablet, while the desktop packages (PyInstaller's datas
# includes plugin/) are fine. That kind of "works on one platform, 404 on the other"
# gap is the hardest to notice, because development looks at the working one.
#
# Shipping it under two names is deliberate: the TXT button is the same file with a
# different extension. Android often refuses .tcl over Bluetooth and accepts .txt, and
# the Python server does exactly the same thing (see /plugin/plugin.tcl.txt).
# 路径要基于 $REPO_ROOT:脚本此时的工作目录是 android/,写相对路径会找不到文件。
# Paths must hang off $REPO_ROOT: the script's working directory here is android/, so a
# relative path looks in the wrong place.
mkdir -p "$GEN/assets/public/plugin"
cp "$REPO_ROOT/plugin/plugin.tcl" "$GEN/assets/public/plugin/plugin.tcl"
cp "$REPO_ROOT/plugin/plugin.tcl" "$GEN/assets/public/plugin/plugin.tcl.txt"

mkdir -p "$GEN/java" "$GEN/res/xml" "$GEN/res/values"
cp -R "$OURS/java/." "$GEN/java/"
cp "$OURS/AndroidManifest.xml" "$GEN/AndroidManifest.xml"
cp "$OURS/res/xml/"*.xml "$GEN/res/xml/" 2>/dev/null || true
# 只覆盖我们自己的 strings.xml,生成工程里的 styles.xml 等保持不动 ——
# 它的 AppTheme 是清单要引用的。
# Only overlay our own strings.xml; the generated styles.xml stays put, since the
# manifest references its AppTheme.
[ -f "$OURS/res/values/strings.xml" ] && cp "$OURS/res/values/strings.xml" "$GEN/res/values/"

# ---- 版本号与签名 ----
#
# 生成工程里写死的是 versionCode 1 / versionName "1.0" —— Capacitor 的模板值,
# 从来不跟 App 版本走,于是所有 APK 在系统看来都是同一个版本,「1.0」。
# 这里每次构建按 print_the_shot_server.py 的 VERSION 改掉。
#
# 签名:release 变体在 Capacitor 模板里**没有签名配置**,assembleRelease 出来的
# 是未签名包 —— 装不上(INSTALL_PARSE_FAILED_NO_CERTIFICATES),而报错信息不会
# 告诉你「你没签名」。用调试密钥签它:sideload 测试够用,而且这个用途下的
# release 包本来就只是 debug 包的一个更快的变体。
#
# ---- version and signing ----
#
# The generated project hard-codes versionCode 1 / versionName "1.0" — Capacitor's
# template values, which never follow the app version, so every APK looks like the same
# release called "1.0". Rewritten here from print_the_shot_server.py's VERSION.
#
# Signing: the release variant has **no signing config** in Capacitor's template, so
# assembleRelease produces an unsigned package that cannot be installed
# (INSTALL_PARSE_FAILED_NO_CERTIFICATES) — and the error never says "you did not sign
# it". The debug key signs it: fine for sideloading, and a release build here is only a
# faster variant of the debug one anyway.
python3 - "$REPO_ROOT" "$VARIANT" <<'PY'
import os, re, sys

root, variant = sys.argv[1], sys.argv[2]
gradle = os.path.join(root, "android", "android", "app", "build.gradle")
src = open(gradle, encoding="utf-8").read()

# 从服务端源码取版本 —— 和界面、发布产物用的是同一个来源
# Take the version from the server source: the same one the UI and releases use
version = None
for line in open(os.path.join(root, "print_the_shot_server.py"), encoding="utf-8"):
    m = re.match(r'VERSION\s*=\s*"([^"]+)"', line.strip())
    if m:
        version = m.group(1)
        break
if not version:
    sys.exit("❌ 读不到 VERSION / could not read VERSION")

# 2.1-beta.3 → code 20103。预发布用它的序号;正式版取同一 minor 下的 99,好让它
# 排在所有同名预发布之后(和 export_strings.py 的版本比较是同一个道理)。
# 2.1-beta.3 → 20103. A prerelease contributes its number; a final release takes 99
# within the same minor so it sorts after every prerelease of that minor (the same
# reasoning as the version comparison in export_strings.py).
m = re.match(r'(\d+)\.(\d+)(?:-[A-Za-z]+\.(\d+))?$', version)
if not m:
    sys.exit("❌ 版本号格式不认识 / unrecognised version: %s" % version)
major, minor, pre = int(m.group(1)), int(m.group(2)), m.group(3)
code = major * 10000 + minor * 100 + (int(pre) if pre else 99)

src = re.sub(r'versionCode\s+\d+', 'versionCode %d' % code, src)
src = re.sub(r'versionName\s+"[^"]*"', 'versionName "%s"' % version, src)

# 先清理上次注入的东西 —— 签名块 **和** release 里那行引用,两样都要删。
#
# 只删块是个真实的坑(踩过):构建过 release 之后再构建 debug,块被删了而那行
# 引用还在,gradle 评估时直接报 unknown property 'debugInjected' —— 而报错指向
# 的是 build.gradle,不是这个脚本,于是看起来像是生成工程坏了。
#
# Clear out whatever a previous run injected — both the block **and** the reference
# inside the release block. Removing only the block is a real trap (this was hit):
# build release, then build debug, and the reference outlives the block, so Gradle
# fails with unknown property 'debugInjected' — pointing at build.gradle rather than
# at this script, which makes it look like the generated project broke.
src = re.sub(r'\n\s*// >>> debug-signing \(injected by build_android.sh\).*?// <<< debug-signing\n',
             '\n', src, flags=re.S)
src = re.sub(r'\n\s*signingConfig signingConfigs\.debugInjected', '', src)

# 签名块总是注入(debug 不引用它就完全无害),只有 release 才加引用。
# 这样两个变体来回构建时,文件状态始终自洽 —— 上一版按变体决定要不要注入块,
# 于是「release 之后 debug」就留下了一个悬空引用。
#
# The block is always injected — harmless when nothing references it — and only the
# release variant references it. That keeps the file consistent no matter which variant
# was built last; deciding the block by variant is what left a dangling reference.
keystore = os.path.expanduser("~/.android/debug.keystore").replace("\\", "/")
block = """
    // >>> debug-signing (injected by build_android.sh)
    // 用调试密钥签 release 包:Capacitor 模板里没有签名配置,不签就装不上
    // (INSTALL_PARSE_FAILED_NO_CERTIFICATES),而那个报错不会告诉你「你没签名」。
    // Signs the release build with the debug key: Capacitor's template has no signing
    // config, and an unsigned package cannot be installed at all.
    signingConfigs {
        debugInjected {
            storeFile file("%s")
            storePassword "android"
            keyAlias "androiddebugkey"
            keyPassword "android"
        }
    }
    // <<< debug-signing
""" % keystore
src = src.replace("\n    buildTypes {", block + "\n    buildTypes {", 1)

if variant == "release":
    src = re.sub(r'(release\s*\{)', r'\1\n            signingConfig signingConfigs.debugInjected', src, count=1)

open(gradle, "w", encoding="utf-8").write(src)
print("   versionName %s / versionCode %d%s" % (version, code,
      " / 签名:调试密钥" if variant == "release" else ""))
PY

# release 用的密钥不存在就生成一个(Android 工具链本来自动建,但 CI 或新机器上可能没有)
# Create the key when absent: the Android tooling normally makes it, but CI or a fresh
# machine may not have one
if [ "$VARIANT" = "release" ] && [ ! -f "$HOME/.android/debug.keystore" ]; then
  mkdir -p "$HOME/.android"
  keytool -genkeypair -v -keystore "$HOME/.android/debug.keystore" \
          -storepass android -alias androiddebugkey -keypass android \
          -keyalg RSA -keysize 2048 -validity 10000 \
          -dname "CN=Android Debug,O=Android,C=US" >/dev/null 2>&1
  echo "   已生成调试密钥 / generated a debug keystore"
fi

# 每次构建都校验一遍 XML:注释里一个 `--` 就足以让 mergeDebugResources 失败,
# 而报错信息指向的是生成的中间文件,不好定位。
# Validate the XML every build: one `--` inside a comment is enough to break
# mergeDebugResources, and the error points at a generated intermediate file,
# which is awkward to trace back.
python3 - <<'PY'
import glob, sys, xml.dom.minidom
files = (glob.glob('android/app/src/main/res/**/*.xml', recursive=True)
         + ['android/app/src/main/AndroidManifest.xml'])
bad = []
for f in files:
    try:
        xml.dom.minidom.parse(f)
    except Exception as e:
        bad.append((f, str(e)[:120]))
if bad:
    print("❌ XML 非法 / invalid XML:")
    for f, e in bad:
        print("   %s\n     %s" % (f, e))
    sys.exit(1)
print("   XML 校验通过 / XML valid (%d files)" % len(files))
PY

# ---- 4. 打包 / package ----
echo "▸ 4/4 Gradle 打包 / packaging with Gradle"
cd android
./gradlew "$GRADLE_TASK" --no-daemon

APK=$(find "app/build/outputs/apk/$VARIANT" -name '*.apk' | head -1)
if [ -z "$APK" ]; then
  echo "❌ 没找到 APK / no APK produced"
  exit 1
fi

OUT="$REPO_ROOT/PrintTheShotNext-${VARIANT}.apk"
cp "$APK" "$OUT"
echo ""
echo "✅ 完成 / done: $OUT"
ls -la "$OUT"
