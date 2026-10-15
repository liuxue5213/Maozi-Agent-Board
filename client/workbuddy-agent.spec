# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules
import platform
import subprocess

native_readers = []
if platform.system() == 'Darwin':
    subprocess.run(['clang', '-fobjc-arc', '-fblocks',
                    'workbuddy/macos_now_playing.m', '-framework', 'Foundation',
                    '-o', 'workbuddy/macos_now_playing'], check=True)
    native_readers.append(('workbuddy/macos_now_playing', 'workbuddy'))
    subprocess.run(['clang', '-std=c11', 'workbuddy/macos_volume.c',
                    '-framework', 'CoreAudio', '-framework', 'AudioToolbox',
                    '-o', 'workbuddy/macos_volume'], check=True)
    native_readers.append(('workbuddy/macos_volume', 'workbuddy'))

hiddenimports = []
hiddenimports += collect_submodules('workbuddy')


a = Analysis(
    ['workbuddy_agent.py'],
    pathex=[],
    binaries=native_readers,
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
    a.binaries,
    a.datas,
    [],
    name='workbuddy-agent',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
