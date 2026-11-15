# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path


REPO_ROOT = Path(SPECPATH).resolve().parents[1]
VERSION_FILE = os.environ.get("U02_VERSION_FILE")
if not VERSION_FILE:
    raise RuntimeError("U02_VERSION_FILE must point to generated version metadata")

a = Analysis(
    [str(REPO_ROOT / "release" / "windows" / "launcher.py")],
    pathex=[str(REPO_ROOT / "src")],
    binaries=[],
    datas=[],
    hiddenimports=[
        "PIL.ImageTk",
        "PIL.TiffImagePlugin",
        "tkinter",
        "tkinter.filedialog",
        "tkinter.messagebox",
        "tkinter.ttk",
    ],
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
    name="LenslessHolographyWorkbench",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    version=VERSION_FILE,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="LenslessHolographyWorkbench",
)
