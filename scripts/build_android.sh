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

mkdir -p "$GEN/java" "$GEN/res/xml" "$GEN/res/values"
cp -R "$OURS/java/." "$GEN/java/"
cp "$OURS/AndroidManifest.xml" "$GEN/AndroidManifest.xml"
cp "$OURS/res/xml/"*.xml "$GEN/res/xml/" 2>/dev/null || true
# 只覆盖我们自己的 strings.xml,生成工程里的 styles.xml 等保持不动 ——
# 它的 AppTheme 是清单要引用的。
# Only overlay our own strings.xml; the generated styles.xml stays put, since the
# manifest references its AppTheme.
[ -f "$OURS/res/values/strings.xml" ] && cp "$OURS/res/values/strings.xml" "$GEN/res/values/"

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
