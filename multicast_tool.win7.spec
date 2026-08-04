# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Windows 7 build.

Same as ``multicast_tool.spec`` but with a couple of knobs for the
PySide2 / Qt 5 toolchain:

  * hiddenimports covers ``PySide2`` instead of ``PySide6``;
  * the resulting binary is named ``MulticastTool-Win7`` to avoid
    clobbering the Win10+ PySide6 build.
"""

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

hiddenimports = []
hiddenimports += collect_submodules('PySide2')

a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'pytest', 'pytest_asyncio', 'unittest', 'pydoc', 'doctest',
        'tkinter', 'matplotlib', 'numpy', 'pandas', 'scipy',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MulticastTool-Win7',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='MulticastTool-Win7',
)
