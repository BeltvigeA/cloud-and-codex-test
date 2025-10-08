from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from client import bambuPrinter


def createSampleThreeMf(targetPath: Path) -> None:
    with zipfile.ZipFile(targetPath, "w") as archive:
        archive.writestr("Metadata/metadata.json", "{}")
        archive.writestr("plate_1.gcode", "G1 X0 Y0\n")


def test_sendBambuPrintJobUsesTemporaryCopy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    originalPath = tmp_path / "sample.3mf"
    createSampleThreeMf(originalPath)
    originalBytes = originalPath.read_bytes()

    uploadCapture: dict[str, object] = {}

    def fakeUploadViaFtps(
        *, ip: str, accessCode: str, localPath: Path, remoteName: str, insecureTls: bool
    ) -> str:
        temporaryLocalPath = Path(localPath)
        uploadCapture["localPath"] = temporaryLocalPath
        uploadCapture["remoteName"] = remoteName
        uploadCapture["temporaryExistsDuringUpload"] = temporaryLocalPath.exists()
        uploadCapture["bytesDuringUpload"] = temporaryLocalPath.read_bytes()
        return "uploaded.3mf"

    monkeypatch.setattr(bambuPrinter, "uploadViaFtps", fakeUploadViaFtps)

    startCapture: dict[str, object] = {}

    def fakeStartPrint(**kwargs) -> None:
        startCapture.update(kwargs)

    monkeypatch.setattr(bambuPrinter, "startPrintViaMqtt", fakeStartPrint)

    options = bambuPrinter.BambuPrintOptions(
        ipAddress="192.168.0.10",
        serialNumber="SERIAL123",
        accessCode="ACCESS",
        useCloud=False,
        waitSeconds=0,
    )

    result = bambuPrinter.sendBambuPrintJob(filePath=originalPath, options=options)

    assert originalPath.read_bytes() == originalBytes
    assert uploadCapture["temporaryExistsDuringUpload"] is True
    assert uploadCapture["localPath"] != originalPath
    assert Path(uploadCapture["localPath"]).parent != originalPath.parent
    assert uploadCapture["bytesDuringUpload"] == originalBytes
    assert result["remoteFile"] == "uploaded.3mf"
    assert result["originalRemoteFile"] == bambuPrinter.buildRemoteFileName(originalPath)
    assert startCapture["paramPath"] == "plate_1.gcode"
    assert startCapture["sdFileName"] == "uploaded.3mf"
