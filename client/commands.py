"""Helpers for interacting with Base44 printer commands."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

import requests

from .base44 import getBaseUrl
from .logbus import log

LOG = logging.getLogger(__name__)


_missingBaseLogged = False


def _resolveBaseUrl() -> str:
    baseUrl = (os.getenv("BASE44_BASE") or "").strip()
    if baseUrl:
        return baseUrl.rstrip("/")

    fallback = (getBaseUrl() or "").strip()
    return fallback.rstrip("/")


def _logMissingBaseUrl() -> None:
    global _missingBaseLogged
    if not _missingBaseLogged:
        LOG.warning(
            "[commands] BASE44_BASE mangler – kommandopoller er deaktivert midlertidig."
        )
        _missingBaseLogged = True


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
        _logMissingBaseUrl()
        return ""
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

    url = _buildUrl(resolvedFunction)
    if not url:
        return None

    payload = {"recipientId": resolvedRecipientId}

    log(
        "INFO",
        "control",
        "poll_start",
        recipientId=resolvedRecipientId,
        url=url,
    )
    started = time.time()

    try:
        response = requests.post(url, json=payload, headers=_buildHeaders(), timeout=10)
        response.raise_for_status()
    except requests.RequestException as error:
        LOG.warning("[commands] %s feilet: %s", resolvedFunction, error)
        log(
            "ERROR",
            "control",
            "poll_failed",
            recipientId=resolvedRecipientId,
            url=url,
            error=str(error),
        )
        return None

    try:
        data = response.json()
    except ValueError:
        LOG.error("[commands] Kunne ikke parse JSON-respons")
        log(
            "ERROR",
            "control",
            "poll_bad_json",
            recipientId=resolvedRecipientId,
            url=url,
        )
        return []

    commands = data.get("commands", []) if isinstance(data, dict) else []
    if not isinstance(commands, list):
        LOG.error("[commands] 'commands' hadde feil format: %r", commands)
        log(
            "ERROR",
            "control",
            "poll_bad_format",
            recipientId=resolvedRecipientId,
            url=url,
            response=data,
        )
        return []

    elapsedMs = int((time.time() - started) * 1000)
    LOG.info(
        "[commands] %d ventende kommandoer for %s",
        len(commands),
        resolvedRecipientId,
    )
    log(
        "INFO",
        "control",
        "poll_ok",
        recipientId=resolvedRecipientId,
        url=url,
        ms=elapsedMs,
        count=len(commands),
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

    url = _buildUrl(resolvedFunction)
    if not url:
        return False

    payload: Dict[str, Any] = {
        "commandId": normalizedCommandId,
        "recipientId": resolvedRecipientId,
        "success": bool(success),
        "error": str(error) if (error and not success) else None,
    }

    log(
        "INFO",
        "control",
        "complete_start",
        commandId=normalizedCommandId,
        success=bool(success),
    )

    try:
        response = requests.post(url, json=payload, headers=_buildHeaders(), timeout=10)
        response.raise_for_status()
    except requests.RequestException as requestError:
        LOG.error("[commands] %s feilet: %s", resolvedFunction, requestError)
        log(
            "ERROR",
            "control",
            "complete_failed",
            commandId=normalizedCommandId,
            success=bool(success),
            error=str(requestError),
        )
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
        log(
            "INFO",
            "control",
            "complete_ok",
            commandId=normalizedCommandId,
            success=bool(success),
        )
        return True

    LOG.error("[commands] Uventet respons ved fullføring: %r", data)
    log(
        "ERROR",
        "control",
        "complete_bad_response",
        commandId=normalizedCommandId,
        success=bool(success),
        response=data,
    )
    return False


__all__ = ["listPendingCommands", "completeCommand"]
