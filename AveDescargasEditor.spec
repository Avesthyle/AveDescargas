# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['media_grabber_editor.py'],
    pathex=[],
    binaries=[],
    datas=[('assets/mediagrabber-icon.png', 'assets')],
    hiddenimports=['tkinterdnd2', 'tkinterdnd2.TkinterDnD'],
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
    name='AveDescargasEditor',
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
    icon='assets/mediagrabber.ico',
)

