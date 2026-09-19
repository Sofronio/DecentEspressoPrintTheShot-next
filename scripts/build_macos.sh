#!/bin/bash
# PrintTheShot Next - macOS 构建脚本 / macOS build script
set -e
cd "$(dirname "$0")/.."   # 仓库根目录

echo "🍳 构建 macOS 版 / Building macOS version..."
if [[ "$(uname)" != "Darwin" ]]; then
    echo "❌ 此脚本只能在 macOS 上运行"
    exit 1
fi

python3 -m venv .build_venv
source .build_venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r scripts/requirements.txt pyinstaller

pyinstaller scripts/print_the_shot.spec --noconfirm
echo "✅ PyInstaller 完成: dist/PrintTheShot"

# 组装 .app(可选,双击启动)
APP="dist/PrintTheShot.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp dist/PrintTheShot "$APP/Contents/MacOS/"
cat > "$APP/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>PrintTheShot</string>
    <key>CFBundleDisplayName</key><string>PrintTheShot Next</string>
    <key>CFBundleIdentifier</key><string>com.printtheshot.neo</string>
    <key>CFBundleVersion</key><string>2.1.0</string>
    <key>CFBundleShortVersionString</key><string>2.1</string>
    <key>CFBundleExecutable</key><string>PrintTheShot</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>NSHighResolutionCapable</key><true/>
    <!-- 这是一个后台服务,用户只用浏览器访问它,没有窗口。声明成代理应用
         (UIElement)才会有正确的行为:
           - 不出现在 Dock 里,也就不存在"图标一直跳"的问题;
           - 不占菜单栏。
         不声明的话:PyInstaller 用 console=True 打的是控制台程序,从不做 Cocoa
         初始化,也就永远不会告诉 macOS"我启动完了"。而 CFBundlePackageType
         又是 APPL,系统把它当图形应用 —— 于是 Dock 图标无限停留在"正在启动"
         状态,一直跳。

         This is a background service the user reaches through a browser; it has no
         window. Declaring it a UIElement (agent app) is what makes its behaviour
         correct: no Dock icon, so no forever-bouncing icon, and no menu bar.
         Without it: PyInstaller with console=True builds a console program that
         never initialises Cocoa and therefore never tells macOS "launch finished".
         CFBundlePackageType is APPL, so the system treats it as a GUI app — and the
         Dock icon sits in the launching state, bouncing, indefinitely. -->
    <key>LSUIElement</key><true/>
    <!-- arm64 二进制与 Python 都需要 macOS 11 以上。原来写的 10.14 是错的,
         会让 10.14/10.15 的用户以为能跑。
         arm64 binaries and Python both require macOS 11+. The previous 10.14 was
         simply wrong and would lead 10.14/10.15 users to believe it would run. -->
    <key>LSMinimumSystemVersion</key><string>11.0</string>
</dict>
</plist>
EOF
chmod +x "$APP/Contents/MacOS/PrintTheShot"

# ---- 必须重新签名 / the bundle MUST be re-signed ----
# PyInstaller 打出来的二进制自带一份「我是 bundle 可执行文件、我有资源」的签名
# (macOS 上 spec 里 argv_emulation=True 会走 bundle 形态的 bootloader)。
# 把这样一个二进制拷进手工拼的壳里,签名就和包对不上了:
#
#     codesign --verify PrintTheShot.app
#     -> code has no resources but signature indicates they must be present
#
# 后果不是警告,是下载者看到「已损坏,无法打开」—— 右键打开也救不了,因为那
# 不是"未验证开发者",是签名本身校验失败。
#
# The PyInstaller binary carries its own bundle-shaped signature (on macOS the
# spec's argv_emulation=True selects the bundle bootloader). Copying such a binary
# into a hand-assembled bundle leaves the two inconsistent:
#
#     codesign --verify PrintTheShot.app
#     -> code has no resources but signature indicates they must be present
#
# The result is not a warning: downloaders see "damaged and can't be opened", and
# right-clicking does not help — this is not an "unidentified developer" block, the
# signature itself fails to validate.
codesign --force --deep --sign - "$APP"
if ! codesign --verify --verbose=1 "$APP" 2>&1 | grep -q "valid on disk"; then
    echo "❌ 签名校验失败,发布出去会显示「已损坏」/ signature invalid; releases would show as damaged"
    codesign --verify --verbose=2 "$APP" || true
    exit 1
fi
echo "✅ 已重新签名并通过校验 / re-signed and verified"

# 打包 .app 为 zip(发布标准形态,避免目录结构在CI汇总时被拍平)
# 用 ditto 而不是 zip:ditto 保留资源分支、扩展属性和权限位,是 macOS 上打包
# .app 的正确工具。zip 会丢掉这些,解压出来的包可能缺权限位。
# ditto rather than zip: ditto preserves resource forks, extended attributes and
# permission bits, which is what .app packaging on macOS requires.
# --sequesterRsrc 是关键,不能省。
#
# 不加这个参数,ditto 会把扩展属性写成 AppleDouble 附属文件(_CodeResources、
# ._PrintTheShot 之类),而且就摆在 .app 包*里面*。ditto 自己解压时认得它们,
# 但 Keka / unzip 这些解压器不认识,会把它们当成普通文件留在包里 —— 于是包的
# 封印被破坏,用户解压后看到「已损坏,无法打开」。
#
# 加了之后这些附属文件被挪到 __MACOSX/(包外面),包本身保持干净,什么解压器
# 解出来都对。
#
# --sequesterRsrc is essential, not optional.
#
# Without it, ditto writes extended attributes as AppleDouble sidecar files
# (_CodeResources, ._PrintTheShot and friends) *inside* the .app. ditto's own
# extractor understands them; Keka and unzip do not, and leave them behind as
# ordinary files inside the bundle. That breaks the seal, and the user sees
# "damaged and can't be opened" after extracting.
#
# With it, the sidecars go to __MACOSX/ outside the bundle, the bundle stays
# clean, and every extractor produces a valid app.
cd dist && ditto -c -k --sequesterRsrc --keepParent PrintTheShot.app PrintTheShot-macos.zip && cd ..
echo "✅ 应用包: $APP → dist/PrintTheShot-macos.zip"
deactivate
