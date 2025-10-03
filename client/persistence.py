"""Persistence helpers for storing print metadata summaries."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Union


def _sanitizeDictionary(candidate: Any) -> Dict[str, Any]:
    if isinstance(candidate, dict):
        return dict(candidate)
    return {}


def storePrintSummary(
    summary: Dict[str, Any],
    baseDirectory: Optional[Union[str, Path]] = None,
) -> Path:
    """Append a metadata-only print summary to a JSON file."""
    allowedKeys: Iterable[str] = (
        "fetchToken",
        "source",
        "fileName",
        "requestMode",
        "printJobId",
        "productId",
    )
    sanitizedSummary: Dict[str, Any] = {
        key: summary[key]
        for key in allowedKeys
        if summary.get(key) is not None
    }
    sanitizedSummary["timestamp"] = summary.get("timestamp", time.time())
    sanitizedSummary["unencryptedData"] = _sanitizeDictionary(summary.get("unencryptedData"))
    sanitizedSummary["decryptedData"] = _sanitizeDictionary(summary.get("decryptedData"))

    storageDirectory = Path(baseDirectory).expanduser().resolve() if baseDirectory else Path.home() / ".printmaster"
    storageDirectory.mkdir(parents=True, exist_ok=True)
    summaryPath = storageDirectory / "print-summaries.json"

    existingEntries: list[Dict[str, Any]] = []
    if summaryPath.exists():
        try:
            with summaryPath.open("r", encoding="utf-8") as summaryFile:
                loaded = json.load(summaryFile)
            if isinstance(loaded, list):
                existingEntries = loaded
        except (OSError, json.JSONDecodeError) as error:
            logging.warning("Unable to load existing print summaries %s: %s", summaryPath, error)

    existingEntries.append(sanitizedSummary)
    with summaryPath.open("w", encoding="utf-8") as summaryFile:
        json.dump(existingEntries, summaryFile, ensure_ascii=False, indent=2)

    logging.info("Stored metadata-only print summary at %s", summaryPath)
    return summaryPath
