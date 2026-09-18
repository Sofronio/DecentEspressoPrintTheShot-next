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
    <key>LSMinimumSystemVersion</key><string>10.14</string>
</dict>
</plist>
EOF
chmod +x "$APP/Contents/MacOS/PrintTheShot"
# 打包 .app 为 zip(发布标准形态,避免目录结构在CI汇总时被拍平)
cd dist && zip -rq PrintTheShot-macos.zip PrintTheShot.app && cd ..
echo "✅ 应用包: $APP → dist/PrintTheShot-macos.zip"
deactivate
