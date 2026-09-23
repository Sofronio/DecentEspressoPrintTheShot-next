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
# 这里每次构建按 print_the_shot_server.py 的 VERSION 与 VERSION_CODE 改掉。
#
# 签名:两个变体都用仓库里那把固定的密钥(android/signing/printtheshot.jks)。
#
# 为什么必须固定 —— 这条是踩出来的
# --------------------------------
# 默认行为下,每个构建环境用自己的调试密钥:本机用 ~/.android/debug.keystore,
# CI 每次跑还在 runner 上现生成一把**新的**。后果是**任何两个包都互相覆盖不了**:
#
#     INSTALL_FAILED_UPDATE_INCOMPATIBLE:
#     Existing package ... signatures do not match newer version
#
# 用户想从上一个版本升级,就只能先卸载 —— 而卸载会**清掉数据**(平板上的历史记录)。
# 发布出去的包每版都这样,等于每版都在逼用户丢一次数据。
#
# 修法:仓库里放一把固定密钥,两个变体都签它,CI 和本机就都一致了。
#
# 取舍要说清楚:这把密钥是**公开的**(在仓库里),所以它保护不了任何东西 ——
# 任何人都能签一个能覆盖安装的包。对这个用途是可接受的(debug 签名的 sideload
# 应用,不走应用商店的信任模型),而「每版都得卸载重装」是实打实的伤害。
# 哪天要上架或要真正的发布签名,应该把它换成 CI secret 里的一把真密钥。
#
# Signing: both variants are signed with the one key committed at
# android/signing/printtheshot.jks.
#
# Why it has to be fixed — learned the hard way
# --------------------------------------------
# By default every build environment uses its own debug key: this machine's
# ~/.android/debug.keystore, and a **freshly generated** one on each CI run. The result is
# that no two builds can replace each other:
#
#     INSTALL_FAILED_UPDATE_INCOMPATIBLE:
#     Existing package ... signatures do not match newer version
#
# Upgrading means uninstalling first, and uninstalling **wipes the data** (the shot
# history on the tablet). Every published build did that to every user.
#
# The fix: one committed key, used by both variants, so CI and local agree.
#
# The trade-off, stated plainly: this key is **public** (it is in the repository), so it
# protects nothing — anyone can sign an APK that installs over the app. That is
# acceptable here (a debug-signed sideload app with no store trust model), whereas
# "uninstall and lose your data on every release" is real harm. Should this ever go to a
# store or need a genuine release signature, move it to a real key in a CI secret.
#
# ---- version and signing ----
#
# The generated project hard-codes versionCode 1 / versionName "1.0" — Capacitor's
# template values, which never follow the app version, so every APK looks like the same
# release called "1.0". Rewritten here from VERSION and VERSION_CODE in
# print_the_shot_server.py.
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

# versionCode 直接从源码里的 VERSION_CODE 取,**不再从版本字符串推导**。
#
# 从前是推导的(2.1-beta.3 → 20103)。版本编号换成 1.0.N 之后那条路会出人命:
# 同一公式对 1.0.2 得 10002,低于所有已装 APK,Android 会以「降级」为由拒绝安装,
# 用户只能卸载重装 —— 而卸载会清掉他所有的 shot 数据。而且那个正则也匹配不了
# 1.0.2 这种格式,构建会直接失败。
#
# 现在它是一个独立的、只增不减的整数(见 print_the_shot_server.py 的 VERSION_CODE)。
# 顺带一提:这一行仍然放在**构建时注入**而不是写进生成的 build.gradle,因为
# android/android/ 是生成工程、不入库。
#
# versionCode comes straight from VERSION_CODE in the source, **no longer derived from
# the version string**.
#
# It used to be derived (2.1-beta.3 → 20103). After the renumbering to 1.0.N that path
# is dangerous: the same formula gives 1.0.2 → 10002, lower than every installed APK,
# so Android refuses the install as a downgrade and the user must uninstall — which
# wipes all their shot data. That regex also cannot match a plain 1.0.2, so the build
# would simply fail.
#
# It is now an independent integer that only ever goes up (see VERSION_CODE in
# print_the_shot_server.py). It is still injected at build time rather than hard-coded
# into build.gradle because android/android/ is a generated, uncommitted project.
code = None
for line in open(os.path.join(root, "print_the_shot_server.py"), encoding="utf-8"):
    m = re.match(r'VERSION_CODE\s*=\s*(\d+)', line.strip())
    if m:
        code = int(m.group(1))
        break
if not code:
    sys.exit("❌ 读不到 VERSION_CODE / could not read VERSION_CODE")

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
src = re.sub(r'\n\s*// >>> (?:debug|shared)-signing \(injected by build_android.sh\).*?// <<< (?:debug|shared)-signing\n',
             '\n', src, flags=re.S)
src = re.sub(r'\n\s*signingConfig signingConfigs\.(?:debugInjected|shared)', '', src)
src = re.sub(r'\n\s*debug \{\n\s*\}\n', '\n', src)   # 上一轮可能补过一个空的 debug 块

# 签名块总是注入(debug 不引用它就完全无害),只有 release 才加引用。
# 这样两个变体来回构建时,文件状态始终自洽 —— 上一版按变体决定要不要注入块,
# 于是「release 之后 debug」就留下了一个悬空引用。
#
# The block is always injected — harmless when nothing references it — and only the
# release variant references it. That keeps the file consistent no matter which variant
# was built last; deciding the block by variant is what left a dangling reference.
keystore = os.path.join(root, "android", "signing", "printtheshot.jks").replace("\\", "/")
if not os.path.exists(keystore):
    sys.exit("❌ 找不到签名密钥 / signing keystore missing: %s" % keystore)

block = """
    // >>> shared-signing (injected by build_android.sh)
    // 仓库里那把固定的密钥,两个变体共用 —— 目的是让本机与 CI 构建出来的包能互相
    // 覆盖安装。用各自环境的调试密钥会出现 INSTALL_FAILED_UPDATE_INCOMPATIBLE,
    // 用户升级只能先卸载,而卸载会清数据。详见 build_android.sh 里的说明。
    //
    // The one committed key, shared by both variants, so builds from this machine and
    // from CI can replace each other. Using each environment's own debug key produces
    // INSTALL_FAILED_UPDATE_INCOMPATIBLE, leaving uninstall (and data loss) as the only
    // way to upgrade. See the note in build_android.sh.
    signingConfigs {
        shared {
            storeFile file("%s")
            storePassword "printtheshot"
            keyAlias "printtheshot"
            keyPassword "printtheshot"
        }
    }
    // <<< shared-signing
""" % keystore
src = src.replace("\n    buildTypes {", block + "\n    buildTypes {", 1)

# 两个变体都要签 —— 而 Capacitor 模板的 buildTypes 里**只有 release,没有 debug**,
# 所以 debug 那个块得先补出来。不补的话,CI 发布的 debug 包(正是发布产物)仍然是
# 每个环境各自的随机调试密钥,「覆盖安装」照样失败。
#
# Both variants — and note Capacitor's template has **only release under buildTypes, no
# debug**, so that block has to be created. Without it the debug APK (which is what CI
# publishes) keeps each environment's own random debug key and cannot be updated in place.
if re.search(r'\n\s*debug\s*\{', src):
    src = re.sub(r'(debug\s*\{)', r'\1\n            signingConfig signingConfigs.shared', src, count=1)
else:
    src = re.sub(r'(buildTypes\s*\{)',
                 r'\1\n        debug {\n            signingConfig signingConfigs.shared\n        }',
                 src, count=1)
src = re.sub(r'(release\s*\{)', r'\1\n            signingConfig signingConfigs.shared', src, count=1)

open(gradle, "w", encoding="utf-8").write(src)
print("   versionName %s / versionCode %d%s" % (version, code,
      " / 签名:仓库固定密钥" if variant == "release" else ""))
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
