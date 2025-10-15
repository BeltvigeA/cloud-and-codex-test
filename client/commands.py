"""Helpers for interacting with Base44 printer commands."""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

import requests

from google.api_core import exceptions as googleApiExceptions
from google.cloud import firestore

from .base44 import getBaseUrl
from .logbus import log

LOG = logging.getLogger(__name__)


_missingBaseLogged = False
_firestoreClientHandle: Optional[firestore.Client] = None


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


def _resolveProjectId() -> str:
    projectId = (os.getenv("FIRESTORE_PROJECT_ID") or os.getenv("GCP_PROJECT_ID") or "").strip()
    return projectId


def _getFirestoreClient() -> Optional[firestore.Client]:
    global _firestoreClientHandle
    if _firestoreClientHandle is not None:
        return _firestoreClientHandle

    projectId = _resolveProjectId()
    if not projectId:
        return None

    try:
        _firestoreClientHandle = firestore.Client(project=projectId)
    except googleApiExceptions.GoogleAPIError as error:
        LOG.error("[commands] Kunne ikke opprette Firestore-klient: %s", error)
        log(
            "ERROR",
            "control",
            "firestore_client_error",
            projectId=projectId,
            error=str(error),
        )
        return None
    except Exception as error:  # pylint: disable=broad-except
        LOG.exception("[commands] Uventet feil ved opprettelse av Firestore-klient")
        log(
            "ERROR",
            "control",
            "firestore_client_exception",
            projectId=projectId,
            error=str(error),
        )
        return None

    return _firestoreClientHandle


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


def _getCommandCollectionName() -> str:
    collectionName = (os.getenv("FIRESTORE_COLLECTION_PRINTER_COMMANDS") or "printer_commands").strip()
    if not collectionName:
        collectionName = "printer_commands"
    return collectionName


def _getFirestoreLimit() -> int:
    limitValue = os.getenv("FIRESTORE_COMMAND_LIMIT", "25").strip()
    try:
        limitSize = max(1, int(limitValue))
    except ValueError:
        limitSize = 25
    return limitSize


def _listPendingCommandsFromFirestore(recipientId: str) -> Optional[List[Dict[str, Any]]]:
    firestoreClient = _getFirestoreClient()
    if firestoreClient is None:
        return None

    collectionName = _getCommandCollectionName()
    limitSize = _getFirestoreLimit()

    log(
        "INFO",
        "control",
        "poll_start",
        recipientId=recipientId,
        source="firestore",
        collection=collectionName,
        limit=limitSize,
    )

    started = time.time()

    try:
        query = (
            firestoreClient.collection(collectionName)
            .where("recipientId", "==", recipientId)
            .where("status", "==", "pending")
            .order_by("createdAt", direction=firestore.Query.ASCENDING)
            .limit(limitSize)
        )
        documents = list(query.stream())
    except googleApiExceptions.GoogleAPICallError as error:
        LOG.error("[commands] Firestore-spørring feilet: %s", error)
        log(
            "ERROR",
            "control",
            "firestore_poll_failed",
            recipientId=recipientId,
            source="firestore",
            collection=collectionName,
            error=str(error),
        )
        return []
    except Exception as error:  # pylint: disable=broad-except
        LOG.exception("[commands] Uventet Firestore-feil under henting av kommandoer")
        log(
            "ERROR",
            "control",
            "firestore_poll_exception",
            recipientId=recipientId,
            source="firestore",
            collection=collectionName,
            error=str(error),
        )
        return []

    elapsedMs = int((time.time() - started) * 1000)

    commands: List[Dict[str, Any]] = []
    for document in documents:
        documentPayload = document.to_dict() or {}
        documentPayload.setdefault("commandId", document.id)
        documentPayload["docId"] = document.id
        commands.append(documentPayload)

    log(
        "INFO",
        "control",
        "poll_payload",
        "Mottok Firestore-kontrollpayload",
        recipientId=recipientId,
        source="firestore",
        collection=collectionName,
        count=len(commands),
        payload=commands,
    )

    log(
        "INFO",
        "control",
        "poll_ok",
        recipientId=recipientId,
        source="firestore",
        collection=collectionName,
        ms=elapsedMs,
        count=len(commands),
    )

    for command in commands:
        log(
            "INFO",
            "control",
            "incoming_detail",
            "Firestore-kontrollkommando mottatt",
            commandId=str(command.get("commandId")),
            commandType=str(command.get("commandType")),
            metadata=command.get("metadata"),
            rawCommand=command,
            source="firestore",
        )

    return commands


def _completeCommandInFirestore(
    commandId: str,
    recipientId: str,
    success: bool,
    error: Optional[str],
) -> Optional[bool]:
    firestoreClient = _getFirestoreClient()
    if firestoreClient is None:
        return None

    collectionName = _getCommandCollectionName()
    updatePayload: Dict[str, Any] = {
        "status": "completed" if success else "failed",
        "completedAt": firestore.SERVER_TIMESTAMP,
        "success": success,
    }
    if error and not success:
        updatePayload["errorMessage"] = error

    log(
        "INFO",
        "control",
        "complete_start",
        commandId=commandId,
        recipientId=recipientId,
        source="firestore",
        status=updatePayload["status"],
    )

    documentRef = firestoreClient.collection(collectionName).document(commandId)

    try:
        documentRef.update(updatePayload)
    except googleApiExceptions.NotFound:
        LOG.warning("[commands] Fant ikke Firestore-kommando %s for oppdatering", commandId)
        log(
            "WARNING",
            "control",
            "firestore_complete_missing",
            commandId=commandId,
            recipientId=recipientId,
            source="firestore",
        )
        return False
    except googleApiExceptions.GoogleAPICallError as apiError:
        LOG.error(
            "[commands] Firestore-oppdatering feilet for %s: %s",
            commandId,
            apiError,
        )
        log(
            "ERROR",
            "control",
            "firestore_complete_failed",
            commandId=commandId,
            recipientId=recipientId,
            source="firestore",
            error=str(apiError),
        )
        return False
    except Exception as exceptionError:  # pylint: disable=broad-except
        LOG.exception(
            "[commands] Uventet feil ved Firestore-oppdatering for %s",
            commandId,
        )
        log(
            "ERROR",
            "control",
            "firestore_complete_exception",
            commandId=commandId,
            recipientId=recipientId,
            source="firestore",
            error=str(exceptionError),
        )
        return False

    log(
        "INFO",
        "control",
        "complete_ok",
        commandId=commandId,
        recipientId=recipientId,
        source="firestore",
        status=updatePayload["status"],
    )

    return True


def listPendingCommands(
    recipientId: Optional[str] = None,
    *,
    functionName: Optional[str] = None,
) -> Optional[List[Dict[str, Any]]]:
    resolvedRecipientId = _resolveRecipientId(recipientId)
    if not resolvedRecipientId:
        LOG.error("[commands] Recipient ID mangler")
        return []

    firestoreCommands = _listPendingCommandsFromFirestore(resolvedRecipientId)
    if firestoreCommands is not None:
        return firestoreCommands

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
        source="http",
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
            source="http",
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
            source="http",
        )
        return []

    log(
        "INFO",
        "control",
        "poll_payload",
        "Mottok kontrollpayload",
        recipientId=resolvedRecipientId,
        url=url,
        status=response.status_code,
        payload=data,
    )

    commands = data.get("commands", []) if isinstance(data, dict) else []
    if not isinstance(commands, list):
        LOG.error("[commands] 'commands' hadde feil format: %r", commands)
        log(
            "ERROR",
            "control",
            "poll_bad_format",
            recipientId=resolvedRecipientId,
            url=url,
            source="http",
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
        source="http",
        ms=elapsedMs,
        count=len(commands),
    )

    for command in commands:
        if isinstance(command, dict):
            log(
                "INFO",
                "control",
                "incoming_detail",
                "Kontrollkommando mottatt",
                commandId=str(command.get("commandId")),
                commandType=str(command.get("commandType")),
                metadata=command.get("metadata"),
                rawCommand=command,
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

    firestoreResult = _completeCommandInFirestore(
        normalizedCommandId,
        resolvedRecipientId,
        bool(success),
        error,
    )
    if firestoreResult is not None:
        return firestoreResult

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
