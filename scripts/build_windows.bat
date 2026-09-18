@echo off
rem PrintTheShot Beta - Windows 构建脚本
rem 注意:全部用 python -m pip(裸 pip 在 Windows venv 里自升级会报
rem "To modify pip, please run..." 错误);每步检查 errorlevel,失败即退出非0
cd /d "%~dp0\.."

echo Building Windows version...
if not exist .build_venv (
    python -m venv .build_venv
    if errorlevel 1 exit /b 1
)
call .build_venv\Scripts\activate.bat
python -m pip install --quiet --upgrade pip
if errorlevel 1 exit /b 1
python -m pip install --quiet -r scripts\requirements.txt pyinstaller
if errorlevel 1 exit /b 1

python -m PyInstaller scripts\print_the_shot.spec --noconfirm
if errorlevel 1 exit /b 1

if not exist dist\PrintTheShot.exe (
    echo ERROR: dist\PrintTheShot.exe not found
    exit /b 1
)
echo Done: dist\PrintTheShot.exe
