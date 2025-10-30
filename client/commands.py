from __future__ import annotations

import logging
import os
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional

import requests

try:  # pragma: no cover - optional dependency
    from google.cloud import firestore  # type: ignore
except ImportError:  # pragma: no cover - gracefully handled in tests
    firestore = None  # type: ignore

_firestoreClientHandle: Optional[Any] = None


def log(level: str, category: str, event: str, message: str = "", **context: Any) -> None:
    logger = logging.getLogger(category or __name__)
    levelName = getattr(logging, level.upper(), logging.INFO)
    logger.log(levelName, "%s %s %s", event, message, context)


def _getFirestoreClient() -> Optional[Any]:
    projectId = os.getenv("FIRESTORE_PROJECT_ID")
    if not projectId:
        return None

    global _firestoreClientHandle  # pylint: disable=global-statement
    if _firestoreClientHandle is not None:
        return _firestoreClientHandle

    if firestore is None:
        return None

    try:
        client = firestore.Client(project=projectId)  # type: ignore[misc]
    except Exception:  # pragma: no cover - import/runtime errors
        return None

    _firestoreClientHandle = client
    return client


def _makeSerializable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day).isoformat()
    if isinstance(value, dict):
        return {key: _makeSerializable(val) for key, val in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_makeSerializable(item) for item in value]
    return value


def _iterPendingDocuments(collection: Any, recipientId: Optional[str]) -> Iterable[Any]:
    query = collection.where("status", "==", "pending")
    if recipientId:
        query = query.where("recipientId", "==", recipientId)

    direction = None
    if firestore is not None:
        direction = getattr(firestore.Query, "ASCENDING", None)
    query = getattr(query, "order_by", lambda *_args, **_kwargs: query)(
        "createdAt", direction=direction
    )
    query = getattr(query, "limit", lambda *_args, **_kwargs: query)(100)
    return query.stream()


def listPendingCommands(recipientId: Optional[str] = None) -> List[Dict[str, Any]]:
    resolvedRecipient = (recipientId or os.getenv("BASE44_RECIPIENT_ID", "")).strip()
    client = _getFirestoreClient()
    if client is None:
        return []

    collectionName = os.getenv("FIRESTORE_COLLECTION_PRINTER_COMMANDS", "printer_commands")
    collection = client.collection(collectionName)

    results: List[Dict[str, Any]] = []
    for document in _iterPendingDocuments(collection, resolvedRecipient or None):
        payload = document.to_dict()
        payload.setdefault("commandId", document.id)
        results.append(payload)
        log(
            "debug",
            "commands",
            "incoming_item",
            commandId=payload.get("commandId"),
            metadata=_makeSerializable(payload.get("metadata", {})),
        )

    log("info", "commands", "poll_ok", count=len(results))
    return results


def _completeCommandInFirestore(
    commandId: str,
    success: bool,
    *,
    recipientId: Optional[str] = None,
    error: Optional[str] = None,
) -> bool:
    client = _getFirestoreClient()
    if client is None:
        return False

    collectionName = os.getenv("FIRESTORE_COLLECTION_PRINTER_COMMANDS", "printer_commands")
    document = client.collection(collectionName).document(commandId)
    statusValue = "completed" if success else "failed"
    payload: Dict[str, Any] = {
        "status": statusValue,
        "success": success,
    }
    if recipientId:
        payload["recipientId"] = recipientId

    if error:
        payload["error"] = error

    if firestore is not None and hasattr(firestore, "SERVER_TIMESTAMP"):
        payload["updatedAt"] = firestore.SERVER_TIMESTAMP

    try:
        document.update(payload)
    except Exception:  # pragma: no cover - propagate but do not fail tests
        return False
    return True


def completeCommand(
    commandId: str,
    success: bool,
    *,
    recipientId: Optional[str] = None,
    error: Optional[str] = None,
) -> bool:
    _completeCommandInFirestore(commandId, success, recipientId=recipientId, error=error)

    baseUrl = os.getenv("BASE44_BASE")
    if not baseUrl:
        return True

    url = baseUrl.rstrip("/") + "/commands/complete"
    payload = {
        "commandId": commandId,
        "success": success,
        "error": error,
        "recipientId": recipientId,
    }
    headers = {"Accept": "application/json"}
    response = requests.post(url, json=payload, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json().get("ok", True)
