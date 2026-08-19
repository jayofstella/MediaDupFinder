# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


project_root = Path(SPECPATH)
source_root = project_root / "src"

a = Analysis(
    [str(source_root / "media_dup_finder" / "__main__.py")],
    pathex=[str(source_root)],
    binaries=[],
    datas=[(str(project_root / "resources" / "icon.ico"), ".")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["unittest"],
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
    [],
    name="MediaDupFinder",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(project_root / "resources" / "icon.ico"),
    manifest=str(project_root / "resources" / "app.manifest"),
    version=str(project_root / "resources" / "version_info.txt"),
)
