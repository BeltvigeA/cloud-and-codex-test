"""Helpers for interacting with Base44 printer commands."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

import requests

LOG = logging.getLogger(__name__)


def _resolveBaseUrl() -> str:
    baseUrl = (os.getenv("BASE44_BASE") or "").strip()
    return baseUrl.rstrip("/")


def _resolveRecipientId(explicitRecipientId: Optional[str] = None) -> str:
    if explicitRecipientId and explicitRecipientId.strip():
        return explicitRecipientId.strip()
    envCandidate = (
        os.getenv("BASE44_RECIPIENT_ID")
        or os.getenv("RECIPIENT_ID")
        or ""
    )
    return envCandidate.strip()


def _buildHeaders() -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    apiKey = (os.getenv("BASE44_API_KEY") or "").strip()
    if apiKey:
        headers["X-API-Key"] = apiKey
    return headers


def _buildUrl(functionName: str) -> str:
    baseUrl = _resolveBaseUrl()
    if not baseUrl:
        raise RuntimeError("BASE44_BASE is not configured")
    return f"{baseUrl}/{functionName}".rstrip("/")


def listPendingCommands(
    recipientId: Optional[str] = None,
    *,
    functionName: Optional[str] = None,
) -> Optional[List[Dict[str, Any]]]:
    resolvedRecipientId = _resolveRecipientId(recipientId)
    if not resolvedRecipientId:
        LOG.error("[commands] Recipient ID mangler")
        return []

    resolvedFunction = (functionName or os.getenv("BASE44_COMMANDS_FN") or "listPendingCommands").strip()
    if not resolvedFunction:
        resolvedFunction = "listPendingCommands"

    try:
        url = _buildUrl(resolvedFunction)
    except Exception as error:  # noqa: BLE001 - configuration error
        LOG.error("[commands] Klarte ikke å bygge URL: %s", error)
        return None

    payload = {"recipientId": resolvedRecipientId}

    try:
        response = requests.post(url, json=payload, headers=_buildHeaders(), timeout=10)
        response.raise_for_status()
    except requests.RequestException as error:
        LOG.warning("[commands] %s feilet: %s", resolvedFunction, error)
        return None

    try:
        data = response.json()
    except ValueError:
        LOG.error("[commands] Kunne ikke parse JSON-respons")
        return []

    commands = data.get("commands", []) if isinstance(data, dict) else []
    if not isinstance(commands, list):
        LOG.error("[commands] 'commands' hadde feil format: %r", commands)
        return []

    LOG.info(
        "[commands] %d ventende kommandoer for %s",
        len(commands),
        resolvedRecipientId,
    )
    return [command for command in commands if isinstance(command, dict)]


def completeCommand(
    commandId: str,
    success: bool,
    *,
    recipientId: Optional[str] = None,
    error: Optional[str] = None,
    functionName: Optional[str] = None,
) -> bool:
    normalizedCommandId = (commandId or "").strip()
    if not normalizedCommandId:
        LOG.error("[commands] commandId mangler ved fullføring")
        return False

    resolvedRecipientId = _resolveRecipientId(recipientId)
    if not resolvedRecipientId:
        LOG.error("[commands] Recipient ID mangler ved fullføring")
        return False

    resolvedFunction = (functionName or os.getenv("BASE44_COMPLETE_CMD_FN") or "completePrinterCommand").strip()
    if not resolvedFunction:
        resolvedFunction = "completePrinterCommand"

    try:
        url = _buildUrl(resolvedFunction)
    except Exception as error:  # noqa: BLE001 - configuration error
        LOG.error("[commands] Klarte ikke å bygge URL: %s", error)
        return False

    payload: Dict[str, Any] = {
        "commandId": normalizedCommandId,
        "recipientId": resolvedRecipientId,
        "success": bool(success),
        "error": str(error) if (error and not success) else None,
    }

    try:
        response = requests.post(url, json=payload, headers=_buildHeaders(), timeout=10)
        response.raise_for_status()
    except requests.RequestException as requestError:
        LOG.error("[commands] %s feilet: %s", resolvedFunction, requestError)
        return False

    try:
        data = response.json() if response.text.strip() else {}
    except ValueError:
        try:
            data = json.loads(response.text)
        except ValueError:
            data = {}

    if isinstance(data, dict) and data.get("ok") is True:
        LOG.info(
            "[commands] Markerte %s som %s",
            normalizedCommandId,
            "completed" if success else "failed",
        )
        return True

    LOG.error("[commands] Uventet respons ved fullføring: %r", data)
    return False


__all__ = ["listPendingCommands", "completeCommand"]
