# -*- mode: python ; coding: utf-8 -*-
# PrintTheShot Next PyInstaller spec
# 无 matplotlib/numpy/pillow,spec 简单得多;内置字体/模板/插件
# No matplotlib/numpy/pillow — the spec is much simpler: bundled fonts, web UI and plugin

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(SPEC)))

a = Analysis(
    [os.path.join(ROOT, 'print_the_shot_server.py')],
    pathex=[ROOT],
    binaries=[],
    datas=[
        (os.path.join(ROOT, 'web', 'fonts'), 'web/fonts'),  # 内置中文字体(随 web 一起)
        (os.path.join(ROOT, 'web'), 'web'),              # 管理界面 + Canvas 绘制
        (os.path.join(ROOT, 'printers'), 'printers'),    # 打印适配器
        (os.path.join(ROOT, 'plugin'), 'plugin'),        # DE1插件
    ],
    hiddenimports=[
        # 打印适配器是按平台动态导入的,静态分析看不到,必须显式声明
        # The printing adapters are imported dynamically by platform, so static
        # analysis cannot see them and they must be declared explicitly here.
        'printers.mac_printer',
        'printers.linux_printer',
        'printers.windows_printer',
        'printers.android_printer',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        'tkinter', 'test', 'pydoc', 'pdb',
        'matplotlib', 'numpy', 'scipy', 'pandas', 'cv2',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    name='PrintTheShot',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,               # 避免杀软误报,关闭UPX
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False if sys.platform != 'darwin' else True,
    target_arch=None,
    codesign_identity=None,
    entitle_file=None,
    icon=None,
)
