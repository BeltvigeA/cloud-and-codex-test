from __future__ import annotations

import importlib
import logging
from pathlib import Path
import sys
import threading
from typing import Any, Dict, List

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from client import command_controller
from client.command_controller import RecipientCommandRouter


class _DummyThread:
    def is_alive(self) -> bool:  # pragma: no cover - simple stub
        return True


def _buildRouter() -> RecipientCommandRouter:
    router = RecipientCommandRouter.__new__(RecipientCommandRouter)
    router.recipientId = "recipient-test"
    router.pollIntervalSeconds = 3.0
    router._lock = threading.Lock()
    router._workers = {}
    router._backlog = []
    router._stopEvent = threading.Event()
    router._thread = _DummyThread()
    router._pollErrorCount = 0
    return router


def test_control_poll_seconds_defaults_to_15(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CONTROL_POLL_SEC", raising=False)
    reloaded = importlib.reload(command_controller)
    assert reloaded._resolveControlPollSeconds() == 15.0  # type: ignore[attr-defined]
    assert reloaded.CONTROL_POLL_SECONDS == 15.0


def test_poll_without_workers_queues_commands(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    router = _buildRouter()

    pendingCommands: List[Dict[str, Any]] = [
        {"commandId": "cmd-001", "metadata": {"printerSerial": "SER-1"}},
        {"commandId": "cmd-002", "metadata": {"printerSerial": "SER-2"}},
    ]

    monkeypatch.setattr(
        "client.command_controller.listPendingCommandsForRecipient",
        lambda recipientId: list(pendingCommands) if recipientId == router.recipientId else [],
    )

    caplog.set_level(logging.INFO)
    router.pollOnce(suppressCheckLog=False)

    assert len(router._backlog) == 2
    assert [entry["commandId"] for entry in router._backlog] == ["cmd-001", "cmd-002"]
    assert any(
        "Queued 2 pending commands for recipient recipient-test (no active printers yet)." in message
        for message in caplog.messages
    )


def test_register_worker_routes_backlog(caplog: pytest.LogCaptureFixture) -> None:
    router = _buildRouter()
    router._backlog = [
        {"commandId": "cmd-100", "metadata": {"printerSerial": "SER-MATCH"}},
        {"commandId": "cmd-200", "metadata": {"printerSerial": "SER-MISSING"}},
    ]

    enqueued: List[Dict[str, Any]] = []

    class FakeWorker:
        def __init__(self, serial: str) -> None:
            self.serial = serial

        def enqueueCommand(self, command: Dict[str, Any]) -> None:
            enqueued.append(command)

    caplog.set_level(logging.DEBUG)
    router.registerWorker(FakeWorker("SER-MATCH"))

    assert [command["commandId"] for command in enqueued] == ["cmd-100"]
    assert [command["commandId"] for command in router._backlog] == ["cmd-200"]
    assert any("Routing queued command cmd-100" in message for message in caplog.messages)
    assert any("No local target for command cmd-200 yet (kept in queue)" in message for message in caplog.messages)


def test_router_ignores_commands_without_matching_serial(caplog: pytest.LogCaptureFixture) -> None:
    router = _buildRouter()
    router._backlog = [
        {"commandId": "cmd-300", "metadata": {"printerIpAddress": "192.168.1.2"}},
        {"commandId": "cmd-400", "metadata": {"serial": "SER-OTHER"}},
    ]

    class FakeWorker:
        def __init__(self) -> None:
            self.serial = "SER-REGISTERED"
            self.received: List[Dict[str, Any]] = []

        def enqueueCommand(self, command: Dict[str, Any]) -> None:
            self.received.append(command)

    worker = FakeWorker()

    caplog.set_level(logging.DEBUG)
    router.registerWorker(worker)

    assert worker.received == []
    assert [command["commandId"] for command in router._backlog] == ["cmd-300", "cmd-400"]
    assert any(
        "No local target for command cmd-300 yet (kept in queue)" in message
        for message in caplog.messages
    )
