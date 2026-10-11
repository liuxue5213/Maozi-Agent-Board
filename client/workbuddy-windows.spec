# -*- mode: python ; coding: utf-8 -*-
# Windows 打包：在 Windows 机器上运行 `pyinstaller workbuddy-windows.spec`。
# 结构与 workbuddy-agent.spec（macOS）一致：onedir 产物 + web_assets 内置。
# 产物：client/dist/workbuddy-agent/workbuddy-agent.exe
# 依赖先行：pip install -r requirements.txt pyinstaller

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = []
hiddenimports += collect_submodules('workbuddy')
# collect.create() 按平台走对应采集器，PyInstaller 只能看到当前平台的静态
# 导入；三平台子模块全列上，spec 在哪个平台跑都成立（导入失败的自动忽略）。
hiddenimports += ['workbuddy.collect.windows', 'workbuddy.collect.macos',
                  'workbuddy.collect.linux']

a = Analysis(
    ['workbuddy_agent.py'],
    pathex=[],
    binaries=[],
    datas=[('workbuddy/web_assets', 'workbuddy/web_assets')],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='workbuddy-agent',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='workbuddy-agent',
)
