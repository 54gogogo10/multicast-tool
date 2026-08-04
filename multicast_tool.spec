# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Multicast Test Tool.

Build with::

    pyinstaller --noconfirm multicast_tool.spec

Outputs to ``dist/MulticastTool/`` (one-dir build for fast startup).

To produce a single-file exe instead, swap the COLLECT block for
``exe = EXE(pyz, a.scripts, a.binaries, a.zipfiles, a.datas, ...``
and remove the COLLECT entirely.
"""

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# Pull in every PySide6 plugin / Qt module so the bundled exe can find
# them at runtime without manual --hidden-import tweaks.
hiddenimports = []
hiddenimports += collect_submodules('PySide6')

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
        # Keep the bundle small by skipping test frameworks and docs.
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
    name='MulticastTool',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,        # GUI app -- no console window
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon='multicast_tool.ico',  # add an .ico here to embed
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='MulticastTool',
)
