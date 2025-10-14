from __future__ import annotations

from typing import Any, Dict, List

import pytest

from client import pending


def test_listPending_accepts_pending_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    capturedCalls: List[Dict[str, Any]] = []

    def fakeCallFunction(
        functionName: str,
        payload: Dict[str, Any],
        *,
        apiKey: str | None = None,
    ) -> Dict[str, Any]:
        capturedCalls.append({
            "functionName": functionName,
            "payload": payload,
            "apiKey": apiKey,
        })
        return {
            "ok": True,
            "pending": [
                {"jobId": "job-1", "fetchToken": "token-1"},
                {"jobId": "job-2", "fetchToken": "token-2"},
            ],
        }

    monkeypatch.setattr(pending, "callFunction", fakeCallFunction)
    monkeypatch.setattr(pending, "getPendingFunctionName", lambda: "listPendingJobs")
    monkeypatch.setattr(pending, "getBaseUrl", lambda: "https://example.invalid")

    result = pending.listPending(" recipient-xyz ", apiKey="secret")

    assert [item["jobId"] for item in result] == ["job-1", "job-2"]
    assert capturedCalls == [
        {
            "functionName": "listPendingJobs",
            "payload": {"recipientId": "recipient-xyz"},
            "apiKey": "secret",
        }
    ]

