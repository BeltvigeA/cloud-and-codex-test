import json
import logging
import sys
import types
from pathlib import Path

import pytest


def installRequestStub() -> None:
    requestsModule = types.ModuleType("requests")

    class DummyResponse:  # pragma: no cover - minimal stub
        def __init__(self, url: str = "", headers: dict | None = None):
            self.url = url
            self.headers = headers or {}

    class DummySession:  # pragma: no cover - minimal stub
        def get(self, *_args, **_kwargs):
            raise NotImplementedError

        def post(self, *_args, **_kwargs):
            raise NotImplementedError

    class DummyRequestException(Exception):
        pass

    requestsModule.Response = DummyResponse
    requestsModule.Session = DummySession
    requestsModule.RequestException = DummyRequestException
    requestsModule.get = lambda *_args, **_kwargs: (_ for _ in ()).throw(NotImplementedError())

    sys.modules.setdefault("requests", requestsModule)


installRequestStub()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from client import client


def createMetadataFile(tmpPath: Path, filename: str, metadata: dict) -> Path:
    metadataPath = tmpPath / filename
    metadataPath.write_text(json.dumps(metadata), encoding="utf-8")
    return metadataPath


def createDataFile(tmpPath: Path, filename: str, content: bytes) -> Path:
    dataPath = tmpPath / filename
    dataPath.write_bytes(content)
    return dataPath


def test_performOfflineFetch_copies_file_with_metadata(tmp_path: Path) -> None:
    metadata = {
        "originalFilename": "customFile.gcode",
        "unencryptedData": {"job": "demo"},
        "decryptedData": {"secret": "value"},
    }
    metadataPath = createMetadataFile(tmp_path, "metadata.json", metadata)
    dataPath = createDataFile(tmp_path, "sourceFile.gcode", b"file-bytes")

    outputDirectory = tmp_path / "output"

    savedFile = client.performOfflineFetch(str(metadataPath), str(dataPath), str(outputDirectory))

    assert savedFile is not None
    assert savedFile.name == "customFile.gcode"
    assert savedFile.exists()
    assert savedFile.read_bytes() == b"file-bytes"


def test_listenOffline_processes_dataset_entries(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    firstMetadata = {
        "originalFilename": "firstFile.gcode",
        "unencryptedData": {"job": "first"},
    }
    secondMetadata = {
        "originalFilename": "secondFile.gcode",
        "decryptedData": {"secret": "second"},
    }

    firstMetadataPath = createMetadataFile(tmp_path, "firstMetadata.json", firstMetadata)
    secondMetadataPath = createMetadataFile(tmp_path, "secondMetadata.json", secondMetadata)

    firstDataPath = createDataFile(tmp_path, "firstData.gcode", b"first")
    secondDataPath = createDataFile(tmp_path, "secondData.gcode", b"second")

    dataset = {
        "pendingFiles": [
            {"metadataFile": str(firstMetadataPath), "dataFile": str(firstDataPath)},
            {
                "metadata": {
                    "originalFilename": "inlineMetadataFile.gcode",
                    "unencryptedData": {"job": "inline"},
                },
                "dataFile": str(secondDataPath),
            },
            {
                "dataFile": str(secondDataPath),
                "metadataFile": str(secondMetadataPath),
            },
        ]
    }

    datasetPath = tmp_path / "dataset.json"
    datasetPath.write_text(json.dumps(dataset), encoding="utf-8")

    outputDirectory = tmp_path / "offlineOutput"
    client.listenOffline(str(datasetPath), str(outputDirectory))

    savedFiles = sorted(path.name for path in outputDirectory.iterdir())
    assert savedFiles == [
        "firstFile.gcode",
        "inlineMetadataFile.gcode",
        "secondFile.gcode",
    ]

    assert any("Offline processing complete" in message for message in caplog.messages)
