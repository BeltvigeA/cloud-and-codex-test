from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from client import base44_client


class _DummyResponse:
    def __init__(self, payload: Any = None) -> None:
        self._payload = payload
        self.content = b"" if payload is None else b"{}"

    def raise_for_status(self) -> None:  # pragma: no cover - no-op for tests
        return

    def json(self) -> Any:
        return self._payload


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BASE44_API_KEY", raising=False)
    monkeypatch.delenv("BASE44_FUNCTIONS_API_KEY", raising=False)
    monkeypatch.delenv("PRINTER_BACKEND_API_KEY", raising=False)


def test_functions_requests_use_dedicated_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BASE44_FUNCTIONS_API_KEY", "functions-key")
    monkeypatch.setenv("BASE44_API_KEY", "legacy-key")
    monkeypatch.setenv("BASE44_RECIPIENT_ID", "recipient-xyz")

    recorded_headers: List[Dict[str, str]] = []

    def fake_post(url: str, json: Dict[str, Any] | None = None, headers: Dict[str, str] | None = None, timeout: float = 0.0) -> _DummyResponse:
        if headers is not None:
            recorded_headers.append(dict(headers))
        return _DummyResponse({})

    monkeypatch.setattr(base44_client.requests, "post", fake_post)

    base44_client.postUpdateStatus({"status": "ready"})
    base44_client.postReportError({"errorMessage": "boom"})

    assert len(recorded_headers) == 2
    assert all(headers.get("X-API-Key") == "functions-key" for headers in recorded_headers)


def test_control_requests_use_control_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRINTER_BACKEND_API_KEY", "control-key")
    monkeypatch.setenv("BASE44_API_KEY", "legacy-key")

    get_calls: List[Tuple[str, Dict[str, str], Dict[str, Any] | None]] = []
    post_calls: List[Tuple[str, Dict[str, str]]] = []

    def fake_get(url: str, headers: Dict[str, str] | None = None, params: Dict[str, Any] | None = None, timeout: float = 0.0) -> _DummyResponse:
        get_calls.append((url, dict(headers or {}), dict(params or {})))
        return _DummyResponse({"commands": []})

    def fake_post(url: str, json: Dict[str, Any] | None = None, headers: Dict[str, str] | None = None, timeout: float = 0.0) -> _DummyResponse:
        post_calls.append((url, dict(headers or {})))
        return _DummyResponse({})

    monkeypatch.setattr(base44_client.requests, "get", fake_get)
    monkeypatch.setattr(base44_client.requests, "post", fake_post)

    commands = base44_client.listPendingCommandsForRecipient("recipient-123")
    assert commands == []

    base44_client.acknowledgeCommand("cmd-1")
    base44_client.postCommandResult("cmd-1", success=True, message="ok")

    assert get_calls and get_calls[0][1].get("X-API-Key") == "control-key"
    assert get_calls[0][0] == "https://printer-backend-934564650450.europe-west1.run.app/control"
    assert get_calls[0][2] == {"recipientId": "recipient-123"}
    assert len(post_calls) == 2
    assert all(headers.get("X-API-Key") == "control-key" for _url, headers in post_calls)


def test_control_headers_fallback_to_legacy_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BASE44_API_KEY", "legacy-key")

    captured_headers: List[Dict[str, str]] = []

    def fake_post(url: str, json: Dict[str, Any] | None = None, headers: Dict[str, str] | None = None, timeout: float = 0.0) -> _DummyResponse:
        captured_headers.append(dict(headers or {}))
        return _DummyResponse({})

    monkeypatch.setattr(base44_client.requests, "post", fake_post)

    base44_client.acknowledgeCommand("cmd-fallback")

    assert captured_headers and captured_headers[0].get("X-API-Key") == "legacy-key"
