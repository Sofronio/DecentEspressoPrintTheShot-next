#!/bin/bash
# PrintTheShot Beta - Linux 构建脚本
set -e
cd "$(dirname "$0")/.."

echo "🍳 构建 Linux 版 / Building Linux version..."
python3 -m venv .build_venv
source .build_venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r scripts/requirements.txt pyinstaller

pyinstaller scripts/print_the_shot.spec --noconfirm
echo "✅ 完成: dist/PrintTheShot"
deactivate
