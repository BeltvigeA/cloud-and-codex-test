from __future__ import annotations

import logging
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from client import bambuPrinter, client  # noqa: E402


def createSampleThreeMf(targetPath: Path) -> None:
    with zipfile.ZipFile(targetPath, "w") as archive:
        archive.writestr("Metadata/metadata.json", "{}")
        archive.writestr("Metadata/plate_1.gcode", "G1 X0 Y0\n")


def test_dispatchBambuPrintExtractsSkippedObjects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    samplePath = tmp_path / "sample.3mf"
    samplePath.write_bytes(b"dummy")

    orderedObjects = [
        {"order": 1, "identify_id": "obj-1", "plate_id": "1", "name": "Object One"},
        {"order": 2, "identify_id": "obj-2", "plate_id": "1", "name": "Object Two"},
    ]
    skippedObjects = [{"order": 2}]

    entryData = {
        "savedFile": str(samplePath),
        "unencryptedData": {
            "printer": {
                "printerSerial": "SERIAL123",
                "ipAddress": "192.168.0.8",
                "accessCode": "ACCESS",
                "brand": "Bambu Lab",
            },
            "slicer": {
                "ordered_objects": orderedObjects,
                "skipped_objects": skippedObjects,
            },
        },
        "decryptedData": {},
    }

    statusPayload = {
        "fileName": "sample.3mf",
        "lastRequestedAt": "2024-01-01T00:00:00Z",
        "requestedMode": "full",
        "success": True,
    }

    configuredPrinters = [
        {
            "serialNumber": "SERIAL123",
            "ipAddress": "192.168.0.8",
            "accessCode": "ACCESS",
            "brand": "Bambu Lab",
        }
    ]

    capturedSkipped: dict[str, Any] = {}

    def fakeSendBambuPrintJob(**kwargs: Any) -> dict[str, Any]:
        capturedSkipped["skippedObjects"] = kwargs.get("skippedObjects")
        return {"remoteFile": "uploaded.3mf", "method": "lan"}

    monkeypatch.setattr(client, "sendBambuPrintJob", fakeSendBambuPrintJob)
    monkeypatch.setattr(client, "sendProductStatusUpdate", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(bambuPrinter.requests, "post", lambda *_args, **_kwargs: None)

    result = client.dispatchBambuPrintIfPossible(
        baseUrl="https://example.com",
        productId="product-1",
        recipientId="recipient-1",
        entryData=entryData,
        statusPayload=statusPayload,
        configuredPrinters=configuredPrinters,
    )

    assert result is not None
    expectedSkipped = [{"order": 2, "identifyId": "obj-2", "plateId": "1", "objectName": "Object Two"}]
    assert capturedSkipped["skippedObjects"] == expectedSkipped


def test_dispatchBambuPrintIncludesPrinterDetailsInErrors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    samplePath = tmp_path / "sample.3mf"
    samplePath.write_bytes(b"dummy")

    entryData = {
        "savedFile": str(samplePath),
        "unencryptedData": {
            "printer": {
                "printerSerial": "SERIAL123",
                "ipAddress": "192.168.0.8",
                "accessCode": "ACCESS",
                "brand": "Bambu Lab",
                "nickname": "Studio Printer",
            }
        },
        "decryptedData": {},
    }

    statusPayload = {
        "fileName": "sample.3mf",
        "lastRequestedAt": "2024-01-01T00:00:00Z",
        "requestedMode": "full",
        "success": True,
    }

    configuredPrinters = [
        {
            "serialNumber": "SERIAL123",
            "ipAddress": "192.168.0.8",
            "accessCode": "ACCESS",
            "brand": "Bambu Lab",
            "nickname": "Studio Printer",
        }
    ]

    def fakeSendBambuPrintJob(**_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("Connection failed for endpoint 192.168.0.8:990")

    monkeypatch.setattr(client, "sendBambuPrintJob", fakeSendBambuPrintJob)
    monkeypatch.setattr(client, "sendProductStatusUpdate", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(bambuPrinter.requests, "post", lambda *_args, **_kwargs: None)

    caplog.set_level(logging.ERROR)

    result = client.dispatchBambuPrintIfPossible(
        baseUrl="https://example.com",
        productId="product-1",
        recipientId="recipient-1",
        entryData=entryData,
        statusPayload=statusPayload,
        configuredPrinters=configuredPrinters,
    )

    assert result is not None
    assert result["success"] is False
    errorMessage = result["error"]
    assert "Studio Printer" in errorMessage
    assert "192.168.0.8" in errorMessage
    assert "Connection failed" in errorMessage
    errorEvents = [event for event in result["events"] if event.get("event") == "error"]
    assert errorEvents and errorEvents[0]["error"] == errorMessage
    assert any("Studio Printer" in record.message and "192.168.0.8" in record.message for record in caplog.records)


def test_dispatchBambuPrintUsesBambulabsApiWhenConfigured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    samplePath = tmp_path / "sample.3mf"
    createSampleThreeMf(samplePath)

    entryData = {
        "savedFile": str(samplePath),
        "unencryptedData": {
            "printer": {
                "serialNumber": "SERIAL999",
                "ipAddress": "192.168.0.9",
                "accessCode": "ACCESS",
                "brand": "Bambu Lab",
            }
        },
        "decryptedData": {},
    }

    statusPayload = {
        "fileName": "sample.3mf",
        "lastRequestedAt": "2024-01-01T00:00:00Z",
        "requestedMode": "full",
        "success": True,
    }

    configuredPrinters = [
        {
            "serialNumber": "SERIAL999",
            "ipAddress": "192.168.0.9",
            "accessCode": "ACCESS",
            "brand": "Bambu Lab",
            "lan_mode": "bambuApi",
        }
    ]

    uploadCapture: dict[str, Any] = {}

    def fakeUploadViaBambulabsApi(
        *, ip: str, serial: str, accessCode: str, localPath: Path, remoteName: str
    ) -> str:
        uploadCapture["ip"] = ip
        uploadCapture["serial"] = serial
        uploadCapture["accessCode"] = accessCode
        uploadCapture["localPathSuffix"] = Path(localPath).suffix
        uploadCapture["remoteName"] = remoteName
        return "uploaded.3mf"

    def failUploadViaFtps(**_kwargs: Any) -> str:
        raise AssertionError("uploadViaFtps should not be used when lanStrategy=bambuApi")

    monkeypatch.setattr(bambuPrinter, "uploadViaBambulabsApi", fakeUploadViaBambulabsApi)
    monkeypatch.setattr(bambuPrinter, "uploadViaFtps", failUploadViaFtps)
    monkeypatch.setattr(bambuPrinter, "startPrintViaMqtt", lambda **_kwargs: None)
    monkeypatch.setattr(client, "sendProductStatusUpdate", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(bambuPrinter.requests, "post", lambda *_args, **_kwargs: None)

    result = client.dispatchBambuPrintIfPossible(
        baseUrl="https://example.com",
        productId="product-2",
        recipientId="recipient-1",
        entryData=entryData,
        statusPayload=statusPayload,
        configuredPrinters=configuredPrinters,
    )

    assert result is not None
    assert result["success"] is True
    assert uploadCapture["ip"] == "192.168.0.9"
    assert uploadCapture["serial"] == "SERIAL999"
    assert uploadCapture["accessCode"] == "ACCESS"
    assert uploadCapture["localPathSuffix"] == ".3mf"
    assert uploadCapture["remoteName"].endswith(".3mf")
