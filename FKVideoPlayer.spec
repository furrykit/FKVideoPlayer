# -*- mode: python ; coding: utf-8 -*-


import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

branding_ico = os.path.join('assets', 'branding', 'icon.ico')
branding_png = os.path.join('assets', 'branding', 'icon.png')

datas = [
    (branding_ico, 'assets/branding'),
    (branding_png, 'assets/branding'),
    (branding_ico, '.'),
] + collect_data_files('_soundfile_data')

ffmpeg_candidate = r'C:\ffmpeg\ffmpeg.exe'
if os.path.exists(ffmpeg_candidate):
    datas.append((ffmpeg_candidate, '.'))

hiddenimports = [
    'fkplayer', 'fkplayer.player', 'fkplayer.app',
    'fkplayer.core', 'fkplayer.core.i18n', 'fkplayer.core.logger', 'fkplayer.core.projects',
    'fkplayer.media', 'fkplayer.media.audio', 'fkplayer.media.capture',
    'fkplayer.ui', 'fkplayer.ui.widgets', 'fkplayer.ui.dialogs',
    'soundcard', 'soundfile', '_soundfile_data', 'cffi'
] + collect_submodules('soundcard') + collect_submodules('fkplayer')

a = Analysis(
    ['player.py'],
    pathex=[],
    binaries=[],
    datas=datas,
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
    name='FKVideoPlayer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[branding_ico],
)
