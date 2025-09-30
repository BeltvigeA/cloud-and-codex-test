# -*- mode: python ; coding: utf-8 -*-
import os
import platform
import shutil
import stat
import subprocess
import time
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

dataResources = [('client', 'client')]
binaryFiles = []
hiddenImports = []
tmpRet = collect_all('PySide6')
dataResources += tmpRet[0]; binaryFiles += tmpRet[1]; hiddenImports += tmpRet[2]


def makeWritable(path: str) -> None:
    try:
        os.chmod(path, stat.S_IWRITE)
    except OSError:
        pass


def removeDistDirectoryWithRetries(targetDirectory: Path, attempts: int = 5) -> None:
    if not targetDirectory.exists():
        return

    def handleRemovalError(function, path, excInfo):
        makeWritable(path)
        try:
            function(path)
        except PermissionError:
            raise excInfo[1]

    for attemptIndex in range(1, attempts + 1):
        try:
            shutil.rmtree(targetDirectory, onerror=handleRemovalError)
            return
        except PermissionError:
            if platform.system() == 'Windows':
                subprocess.run(
                    ['taskkill', '/f', '/im', 'PrintMasterDashboard.exe'],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            time.sleep(0.5 * attemptIndex)

    raise PermissionError(f'Unable to remove locked directory: {targetDirectory}')


specDirectory = Path(__file__).resolve().parent
distDirectory = specDirectory / 'dist' / 'PrintMasterDashboard'
removeDistDirectoryWithRetries(distDirectory)


a = Analysis(
    ['client\\gui_app.py'],
    pathex=[],
    binaries=binaryFiles,
    datas=dataResources,
    hiddenimports=hiddenImports,
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
    name='PrintMasterDashboard',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
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
    upx=True,
    upx_exclude=[],
    name='PrintMasterDashboard',
)
