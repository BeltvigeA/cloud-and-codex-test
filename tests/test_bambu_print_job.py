from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from client import bambuPrinter


def createSampleThreeMf(targetPath: Path) -> None:
    with zipfile.ZipFile(targetPath, "w") as archive:
        archive.writestr("Metadata/metadata.json", "{}")
        archive.writestr("plate_1.gcode", "G1 X0 Y0\n")


def createSampleThreeMfWithSliceInfo(targetPath: Path) -> None:
    sliceInfo = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<config>\n  <plate>\n    <metadata key=\"index\" value=\"1\" />\n    <ordered_objects>\n      <object order=\"1\" identify_id=\"obj-1\" name=\"Object One\" />\n      <object order=\"2\" identify_id=\"obj-2\" name=\"Object Two\" />\n    </ordered_objects>\n    <object identify_id=\"obj-1\" name=\"Object One\" skipped=\"false\" />\n    <object identify_id=\"obj-2\" name=\"Object Two\" skipped=\"false\" />\n  </plate>\n</config>\n"""
    with zipfile.ZipFile(targetPath, "w") as archive:
        archive.writestr("Metadata/slice_info.config", sliceInfo)
        archive.writestr("Metadata/metadata.json", "{}")
        archive.writestr("Metadata/plate_1.gcode", "G1 X0 Y0\n")


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


def test_sendBambuPrintJobMarksSkippedObjects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    samplePath = tmp_path / "skip.3mf"
    createSampleThreeMfWithSliceInfo(samplePath)

    uploadCapture: dict[str, object] = {}

    def fakeUploadViaFtps(
        *, ip: str, accessCode: str, localPath: Path, remoteName: str, insecureTls: bool
    ) -> str:
        with zipfile.ZipFile(localPath, "r") as archive:
            uploadCapture["sliceInfo"] = archive.read("Metadata/slice_info.config")
        uploadCapture["remoteName"] = remoteName
        return "uploaded.3mf"

    monkeypatch.setattr(bambuPrinter, "uploadViaFtps", fakeUploadViaFtps)

    def fakeStartPrint(**kwargs) -> None:
        return None

    monkeypatch.setattr(bambuPrinter, "startPrintViaMqtt", fakeStartPrint)

    options = bambuPrinter.BambuPrintOptions(
        ipAddress="192.168.0.5",
        serialNumber="SERIAL-SKIP",
        accessCode="ACCESS",
        useCloud=False,
        waitSeconds=0,
    )

    skipTargets = [{"order": 2, "plateId": "1", "identifyId": "obj-2", "objectName": "Object Two"}]

    bambuPrinter.sendBambuPrintJob(
        filePath=samplePath,
        options=options,
        skippedObjects=skipTargets,
    )

    sliceInfoBytes = uploadCapture.get("sliceInfo")
    assert isinstance(sliceInfoBytes, bytes)
    xmlRoot = ET.fromstring(sliceInfoBytes.decode("utf-8"))
    plateElement = xmlRoot.find("plate")
    assert plateElement is not None
    objectOne = None
    objectTwo = None
    for objectElement in plateElement.findall("object"):
        identifyId = objectElement.get("identify_id")
        if identifyId == "obj-1":
            objectOne = objectElement
        if identifyId == "obj-2":
            objectTwo = objectElement
    assert objectOne is not None
    assert objectTwo is not None
    assert objectOne.get("skipped") == "false"
    assert objectTwo.get("skipped") == "true"

    skippedContainer = plateElement.find("skipped_objects")
    assert skippedContainer is not None
    skippedOrders = {element.get("order") for element in skippedContainer.findall("object")}
    assert skippedOrders == {"2"}


def test_sendBambuPrintJobWrapsGcodeInThreeMf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gcodePath = tmp_path / "model.gcode"
    gcodeContent = "G1 X5 Y5\nM400\n"
    gcodePath.write_text(gcodeContent)

    uploadCapture: dict[str, object] = {}

    def fakeUploadViaFtps(
        *, ip: str, accessCode: str, localPath: Path, remoteName: str, insecureTls: bool
    ) -> str:
        uploadCapture["remoteName"] = remoteName
        uploadCapture["localPath"] = localPath
        with zipfile.ZipFile(localPath, "r") as archive:
            with archive.open("Metadata/plate_1.gcode") as handle:
                uploadCapture["gcodeBytes"] = handle.read()
        return "model.3mf"

    monkeypatch.setattr(bambuPrinter, "uploadViaFtps", fakeUploadViaFtps)

    def fakeStartPrint(**kwargs) -> None:
        return None

    monkeypatch.setattr(bambuPrinter, "startPrintViaMqtt", fakeStartPrint)

    options = bambuPrinter.BambuPrintOptions(
        ipAddress="192.168.1.5",
        serialNumber="SERIAL-GCODE",
        accessCode="ACCESS",
        useCloud=False,
        waitSeconds=0,
    )

    bambuPrinter.sendBambuPrintJob(filePath=gcodePath, options=options)

    capturedRemote = uploadCapture.get("remoteName")
    assert capturedRemote == "model.3mf"
    localPath = uploadCapture.get("localPath")
    assert isinstance(localPath, Path)
    assert localPath.suffix == ".3mf"
    capturedBytes = uploadCapture.get("gcodeBytes")
    assert capturedBytes == gcodeContent.encode("utf-8")


def test_applySkippedObjectsToArchiveRejectsUnknownOrder(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    samplePath = tmp_path / "invalid.3mf"
    createSampleThreeMfWithSliceInfo(samplePath)

    caplog.set_level("ERROR")

    with pytest.raises(ValueError) as errorInfo:
        bambuPrinter.applySkippedObjectsToArchive(
            samplePath,
            [{"order": 99, "identifyId": "missing"}],
        )

    assert "Unable to locate slicer objects" in str(errorInfo.value)
    assert "Unable to locate slicer objects" in caplog.text
