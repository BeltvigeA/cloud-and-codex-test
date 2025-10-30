from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import requests


def listPending(
    recipientId: str,
    *,
    baseUrl: Optional[str] = None,
    apiKey: Optional[str] = None,
    timeout: int = 30,
) -> List[Dict[str, Any]]:
    from . import client as clientModule

    resolvedRecipient = (recipientId or "").strip()
    if not resolvedRecipient:
        raise ValueError("recipientId must be provided")

    resolvedBaseUrl = (baseUrl or os.getenv("BASE44_BASE", "")).rstrip("/")
    if not resolvedBaseUrl:
        raise RuntimeError("BASE44_BASE must be configured")

    url = clientModule.buildPendingUrl(resolvedBaseUrl, resolvedRecipient)
    headers = {"Accept": "application/json"}
    if apiKey:
        headers["X-API-Key"] = apiKey
    response = requests.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    return list(payload.get("pendingFiles", []))
