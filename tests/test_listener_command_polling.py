from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, List

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from client.command_poller import CommandPoller
from client.gui import ListenerGuiApp


class FakeCommandPoller:
    def __init__(self) -> None:
        self.startCalls: List[str] = []
        self.stopCalls = 0
        self.recipientId = ""

    def start(self, recipientId: str) -> None:
        self.startCalls.append(recipientId)

    def stop(self) -> None:
        self.stopCalls += 1

    def setRecipientId(self, recipientId: str) -> None:
        self.recipientId = recipientId


class FakeBase44Reporter:
    def __init__(self) -> None:
        self.startCalls: List[tuple[str, str | None]] = []
        self.stopCalls = 0

    def start(self, recipientId: str, apiKey: str | None = None) -> None:
        self.startCalls.append((recipientId, apiKey))

    def stop(self) -> None:
        self.stopCalls += 1


@pytest.fixture
def listenerApp() -> ListenerGuiApp:
    app = ListenerGuiApp.__new__(ListenerGuiApp)
    app.listenerActive = False
    app.listenerReady = False
    app.listenerRecipientId = ""
    app.listenerStatusApiKey = "status-key"
    app.base44ReporterActive = False
    app.base44Reporter = FakeBase44Reporter()
    app.commandPoller = FakeCommandPoller()
    app._snapshotPrintersForBase44 = lambda: []
    app._resolveStatusApiKey = lambda: "status-key"
    return app


def testListenerCommandPollerRunsWhenBase44Inactive(listenerApp: ListenerGuiApp) -> None:
    listenerApp.listenerActive = True
    listenerApp.listenerReady = True
    listenerApp.listenerRecipientId = "recipient-123"
    listenerApp._snapshotPrintersForBase44 = lambda: []

    listenerApp._updateStatusReporterState()

    assert listenerApp.base44ReporterActive is False
    assert listenerApp.commandPoller.startCalls == ["recipient-123"]
    assert listenerApp.commandPoller.stopCalls == 0


def testListenerBase44ActivationStopsCommandPoller(listenerApp: ListenerGuiApp) -> None:
    listenerApp.listenerActive = True
    listenerApp.listenerReady = True
    listenerApp.listenerRecipientId = "recipient-789"

    listenerApp._snapshotPrintersForBase44 = lambda: [{"mqttReady": True}]

    listenerApp._updateStatusReporterState()

    assert listenerApp.base44ReporterActive is True
    assert listenerApp.base44Reporter.startCalls == [("recipient-789", "status-key")]
    assert listenerApp.commandPoller.stopCalls == 1

    listenerApp._snapshotPrintersForBase44 = lambda: []
    listenerApp._updateStatusReporterState()

    assert listenerApp.base44ReporterActive is False
    assert listenerApp.base44Reporter.stopCalls == 1
    assert listenerApp.commandPoller.startCalls[-1] == "recipient-789"


def testCommandPollerInvokesListPendingOncePerInterval(monkeypatch: pytest.MonkeyPatch) -> None:
    sleepCount = 0
    sleepDurations: List[float] = []

    def fakeSleep(duration: float) -> None:
        nonlocal sleepCount
        sleepCount += 1
        sleepDurations.append(duration)

    poller = CommandPoller(intervalSec=0.3, sleepCallable=fakeSleep)
    callMarkers: List[int] = []

    def fakeListPendingCommands(recipientId: str) -> list[Any]:
        callMarkers.append(sleepCount)
        if len(callMarkers) >= 2:
            poller._stopEvent.set()
        return []

    monkeypatch.setattr("client.command_poller.listPendingCommands", fakeListPendingCommands)

    poller._recipientId = "recipient-test"
    poller._stopEvent.clear()
    poller._run()

    assert callMarkers == [0, max(1, int(poller._intervalSeconds * 10))]
    assert all(duration == pytest.approx(0.1) for duration in sleepDurations)
