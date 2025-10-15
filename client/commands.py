"""Helpers for interacting with Base44 printer commands."""

from __future__ import annotations

import json
import logging
import os
from copy import deepcopy
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

import requests

from google.api_core import exceptions as googleApiExceptions
from google.cloud import firestore

try:  # pragma: no cover - optional dependency availability varies in tests
    from google.cloud.firestore_v1 import _helpers as firestoreHelpers  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - fallback when Firestore helpers missing
    firestoreHelpers = None  # type: ignore[assignment]


_FIRESTORE_TIMESTAMP_TYPES: Tuple[type, ...] = ()
if firestoreHelpers is not None:
    timestampTypes = []
    for helperName in ("Timestamp", "DatetimeWithNanoseconds"):
        helperType = getattr(firestoreHelpers, helperName, None)
        if helperType is not None:
            timestampTypes.append(helperType)
    _FIRESTORE_TIMESTAMP_TYPES = tuple(timestampTypes)


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
        log(
            "ERROR",
            "control",
            "firestore_client_error",
            source="firestore",
            error="missing_project_id",
        )
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


def _sanitizeLogValue(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _sanitizeLogValue(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitizeLogValue(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitizeLogValue(item) for item in value]
    if isinstance(value, set):
        return [_sanitizeLogValue(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if _FIRESTORE_TIMESTAMP_TYPES and isinstance(value, _FIRESTORE_TIMESTAMP_TYPES):
        if hasattr(value, "isoformat"):
            try:
                return value.isoformat()  # type: ignore[no-any-return]
            except TypeError:
                pass
        if hasattr(value, "to_datetime"):
            converted = value.to_datetime()  # type: ignore[call-arg]
            if isinstance(converted, (datetime, date)):
                return converted.isoformat()
    return value


def _makeLoggableCommand(command: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(command, dict):
        return {}
    sanitizedCommand = deepcopy(command)
    for key, value in sanitizedCommand.items():
        sanitizedCommand[key] = _sanitizeLogValue(value)
    return sanitizedCommand


def _listPendingCommandsFromFirestore(recipientId: str, limit: int = 25) -> list[dict[str, Any]]:
    """
    Fetch pending commands from Firestore. If the ordered query requires a composite index,
    fall back to a simpler query (without order_by) and sort in memory.
    Always emits control-category logs so the GUI shows what happened.
    """
    from google.cloud import firestore  # local import to avoid hard dep on import time
    log("INFO", "control", "poll_start",
        source="firestore", recipientId=recipientId,
        collection=firestoreCollectionPrinterCommands, limit=limit)

    fc = _getFirestoreClient()
    if fc is None:
        log("ERROR", "control", "firestore_client_error", recipientId=recipientId)
        return []

    # First try: with order_by createdAt (preferred)
    docs = None
    try:
        q = (fc.collection(firestoreCollectionPrinterCommands)
             .where("recipientId", "==", recipientId)
             .where("status", "==", "pending")
             .order_by("createdAt", direction=firestore.Query.DESCENDING)
             .limit(limit))
        docs = list(q.stream())
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        log("ERROR", "control", "_listPendingCommandsFromFirestore",
            f"[commands] Firestore-spørring feilet: {msg}")
        log("ERROR", "control", "firestore_poll_failed",
            recipientId=recipientId, collection=firestoreCollectionPrinterCommands,
            source="firestore", error=msg)

        # Fallback if index missing: remove order_by and sort in memory
        if "requires an index" in msg.lower():
            try:
                q2 = (fc.collection(firestoreCollectionPrinterCommands)
                      .where("recipientId", "==", recipientId)
                      .where("status", "==", "pending")
                      .limit(limit))
                docs = list(q2.stream())
                # sort in memory by createdAt desc if available
                def _created_at(doc):
                    data = doc.to_dict() or {}
                    ts = data.get("createdAt")
                    try:
                        # Firestore Timestamp has to_datetime()
                        return ts.to_datetime() if hasattr(ts, "to_datetime") else ts
                    except Exception:
                        return None
                docs.sort(key=_created_at, reverse=True)
                log("INFO", "control", "firestore_fallback_used",
                    recipientId=recipientId, collection=firestoreCollectionPrinterCommands,
                    reason="missing_index", returned=len(docs))
            except Exception as e2:  # noqa: BLE001
                log("ERROR", "control", "firestore_poll_failed_no_orderby",
                    recipientId=recipientId, collection=firestoreCollectionPrinterCommands,
                    source="firestore", error=str(e2))
                docs = []  # ensure not None
        else:
            docs = []  # non-index error -> return empty list

    # Build rows
    rows: list[dict[str, Any]] = []
    for d in docs or []:
        data = d.to_dict() or {}
        data["commandId"] = data.get("commandId") or getattr(d, "id", None)
        rows.append(data)

    log("INFO", "control", "poll_ok",
        source="firestore", recipientId=recipientId,
        collection=firestoreCollectionPrinterCommands, count=len(rows))

    for r in rows[:10]:
        log("INFO", "control", "incoming_item",
            commandId=r.get("commandId"),
            commandType=r.get("commandType"),
            recipientId=recipientId)

    return rows
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
) -> List[Dict[str, Any]]:
    resolvedRecipientId = _resolveRecipientId(recipientId)
    if not resolvedRecipientId:
        LOG.error("[commands] Recipient ID mangler")
        return []

    return _listPendingCommandsFromFirestore(
        resolvedRecipientId,
        limitSize=_getFirestoreLimit(),
    )


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
