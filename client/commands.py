"""Helpers for interacting with Base44 printer commands."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from .base44 import callFunction

LOG = logging.getLogger(__name__)

_DEFAULT_COMMANDS_FUNCTION = "listPendingCommands"
_DEFAULT_COMPLETE_FUNCTION = "completePrinterCommand"


def _resolveRecipientId(explicitRecipientId: Optional[str] = None) -> str:
    candidate = (explicitRecipientId or "").strip()
    if candidate:
        return candidate
    envCandidate = (
        os.getenv("BASE44_RECIPIENT_ID")
        or os.getenv("RECIPIENT_ID")
        or ""
    )
    return envCandidate.strip()


def getCommandsFunctionName() -> str:
    """Return the configured Base44 commands function name."""

    return os.getenv("BASE44_COMMANDS_FN", _DEFAULT_COMMANDS_FUNCTION)


def getCompleteFunctionName() -> str:
    """Return the configured Base44 complete-command function name."""

    return os.getenv("BASE44_COMPLETE_CMD_FN", _DEFAULT_COMPLETE_FUNCTION)


def listPendingCommands(
    recipientId: Optional[str] = None,
    *,
    functionName: Optional[str] = None,
) -> Optional[List[Dict[str, Any]]]:
    """Fetch pending commands for the configured recipient.

    Returns a list of commands when the request succeeds. ``None`` indicates that
    the remote call failed and should be retried later. An empty list means the
    call was successful but no commands were available.
    """

    resolvedRecipientId = _resolveRecipientId(recipientId)
    if not resolvedRecipientId:
        LOG.error("[commands] Recipient ID mangler")
        return []

    targetFunction = (functionName or getCommandsFunctionName()).strip() or _DEFAULT_COMMANDS_FUNCTION
    payload = {"recipientId": resolvedRecipientId}
    responseData = callFunction(targetFunction, payload)

    if responseData is None:
        LOG.warning(
            "[commands] %s ga ingen respons for %s", targetFunction, resolvedRecipientId
        )
        return None

    if not isinstance(responseData, dict):
        LOG.error(
            "[commands] Uventet respons fra %s: %r",
            targetFunction,
            responseData,
        )
        return []

    commands = responseData.get("commands") or []
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
    """Mark a command as completed or failed in Base44."""

    normalizedCommandId = (commandId or "").strip()
    if not normalizedCommandId:
        LOG.error("[commands] commandId mangler ved fullføring")
        return False

    resolvedRecipientId = _resolveRecipientId(recipientId)
    if not resolvedRecipientId:
        LOG.error("[commands] Recipient ID mangler ved fullføring")
        return False

    targetFunction = (functionName or getCompleteFunctionName()).strip() or _DEFAULT_COMPLETE_FUNCTION
    payload: Dict[str, Any] = {
        "commandId": normalizedCommandId,
        "recipientId": resolvedRecipientId,
        "success": bool(success),
    }
    if not success and error:
        payload["error"] = str(error)
    else:
        payload["error"] = None

    responseData = callFunction(targetFunction, payload)
    if isinstance(responseData, dict) and responseData.get("ok") is True:
        LOG.info(
            "[commands] Markerte %s som %s",
            normalizedCommandId,
            "completed" if success else "failed",
        )
        return True

    LOG.error(
        "[commands] Klarte ikke markere %s: %r",
        normalizedCommandId,
        responseData,
    )
    return False


# snake_case compatibility exports
list_pending_commands = listPendingCommands
complete_command = completeCommand
