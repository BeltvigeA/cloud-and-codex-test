from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from client import pending


def test_listPending_accepts_pending_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    capturedRequests: List[Dict[str, Any]] = []

    def fakeBuildPendingUrl(baseUrl: str, recipientId: str) -> str:
        return f"{baseUrl}/recipients/{recipientId}/pending"

    class DummyResponse:
        def raise_for_status(self) -> None:  # pragma: no cover - no-op
            return None

        def json(self) -> Dict[str, Any]:
            return {
                "pendingFiles": [
                    {"jobId": "job-1", "fetchToken": "token-1"},
                    {"jobId": "job-2", "fetchToken": "token-2"},
                ]
            }

    def fakeRequestsGet(url: str, *, headers: Dict[str, str], timeout: int) -> DummyResponse:
        capturedRequests.append({
            "url": url,
            "headers": headers,
            "timeout": timeout,
        })
        return DummyResponse()

    monkeypatch.setattr("client.client.buildPendingUrl", fakeBuildPendingUrl)
    monkeypatch.setattr(pending.requests, "get", fakeRequestsGet)

    result = pending.listPending(
        " recipient-xyz ",
        baseUrl="https://example.invalid",
        apiKey="secret",
    )

    assert [item["jobId"] for item in result] == ["job-1", "job-2"]
    assert capturedRequests == [
        {
            "url": "https://example.invalid/recipients/recipient-xyz/pending",
            "headers": {"Accept": "application/json", "X-API-Key": "secret"},
            "timeout": 30,
        }
    ]

