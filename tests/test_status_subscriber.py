from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

from client.status_subscriber import BambuStatusSubscriber


def test_status_subscriber_finalize_timelapse(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    events: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    errors: List[Tuple[str, Dict[str, Any]]] = []

    subscriber = BambuStatusSubscriber(
        onUpdate=lambda status, config: events.append((status, config)),
        onError=lambda message, config: errors.append((message, config)),
    )

    cachePath = tmp_path / "command-cache.json"
    monkeypatch.setattr("client.status_subscriber.CACHE_DIRECTORY", tmp_path)
    monkeypatch.setattr("client.status_subscriber.CACHE_FILE_PATH", cachePath)

    cachePayload = {
        "timelapse_sessions": {
            "SERIAL123": {"directory": str(tmp_path / "timelapse")}
        }
    }
    cachePath.write_text(json.dumps(cachePayload), encoding="utf-8")

    class FakeCamera:
        def __init__(self) -> None:
            self.stopCalls: List[str] = []

        def stop_timelapse_capture(self) -> None:
            self.stopCalls.append("stop_timelapse_capture")

    class FakePrinter:
        def __init__(self) -> None:
            self.camera_client = FakeCamera()

    fakePrinter = FakePrinter()
    printerConfig = {"serialNumber": "SERIAL123"}

    subscriber._finalizeTimelapseSession("SERIAL123", fakePrinter, printerConfig)

    assert fakePrinter.camera_client.stopCalls == ["stop_timelapse_capture"]
    assert events and events[0][0]["status"] == "timelapseSaved"
    assert events[0][0]["directory"] == str(tmp_path / "timelapse")
    cacheContents = json.loads(cachePath.read_text(encoding="utf-8"))
    assert cacheContents.get("timelapse_sessions", {}).get("SERIAL123") is None
    assert not errors
