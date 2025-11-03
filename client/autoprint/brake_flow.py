from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional

from .plate_inspector import compareToReference

log = logging.getLogger(__name__)

SnapshotCaptureFunc = Callable[[Any, str], Path]


@dataclass(frozen=True)
class BrakeFlowContext:
    serial: str
    ipAddress: Optional[str]
    jobKey: str
    enableBrakePlate: bool
    platesRequested: int
    printZHeight: Optional[float] = None
    checkpointPaths: Mapping[int, Path] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def shouldTrigger(self) -> bool:
        return bool(self.enableBrakePlate) or int(self.platesRequested or 0) > 1

    def normalizedJobKey(self) -> str:
        candidate = str(self.jobKey or "job").strip()
        sanitized = "".join(character if character.isalnum() or character in {"-", "_"} else "_" for character in candidate)
        return sanitized or "job"

    def resolvedCheckpointPercents(self, defaults: tuple[int, ...]) -> list[int]:
        percents: set[int] = set(defaults)
        for key in self.checkpointPaths.keys():
            normalized = BrakeFlow._normalizePercent(key)  # type: ignore[attr-defined]
            if normalized is not None:
                percents.add(normalized)
        return sorted(percents)


class BrakeFlow:
    MAX_ATTEMPTS = 2
    COOL_THRESHOLD_C = 30.0
    COOLDOWN_TIMEOUT_SECONDS = 900.0
    COOLDOWN_POLL_SECONDS = 5.0
    RETRY_DELAY_SECONDS = 20.0
    INSPECTION_SNAPSHOT_DELAY_SECONDS = 0.5
    DEFAULT_CHECKPOINTS = (0, 33, 66, 100)

    @classmethod
    def run_demo(
        cls,
        printer: Any,
        context: BrakeFlowContext,
        captureFunc: SnapshotCaptureFunc,
    ) -> bool:
        if not context.shouldTrigger():
            log.debug(
                "[brake] skipping brake demo for %s (platesRequested=%s enableBrakePlate=%s)",
                context.serial,
                context.platesRequested,
                context.enableBrakePlate,
            )
            return True
        log.info("[brake] starting brake demo for %s job=%s", context.serial, context.normalizedJobKey())
        attempts = max(1, cls.MAX_ATTEMPTS)
        for attempt in range(1, attempts + 1):
            cooldownOk = cls._waitForCooldown(printer, context)
            if not cooldownOk:
                log.warning("[brake] cooldown timeout for %s; continuing with inspection", context.serial)
            cls._homeXY(printer, context)
            if cls._performInspection(printer, context, captureFunc, attempt):
                log.info("[brake] plate clear for %s after attempt %d", context.serial, attempt)
                return True
            if attempt < attempts:
                log.warning("[brake] obstruction detected for %s (attempt %d)", context.serial, attempt)
                time.sleep(cls.RETRY_DELAY_SECONDS)
        log.error("[brake] plate obstructed for %s after %d attempt(s)", context.serial, attempts)
        return False

    @classmethod
    def runDemo(
        cls,
        printer: Any,
        context: BrakeFlowContext,
        captureFunc: SnapshotCaptureFunc,
    ) -> bool:
        return cls.run_demo(printer, context, captureFunc)

    @classmethod
    def _waitForCooldown(cls, printer: Any, context: BrakeFlowContext) -> bool:
        deadline = time.monotonic() + cls.COOLDOWN_TIMEOUT_SECONDS
        lastLogged = 0.0
        while True:
            temperature = cls._readBedTemperature(printer)
            if temperature is not None:
                if temperature <= cls.COOL_THRESHOLD_C:
                    log.info("[brake] bed cooled to %.1f°C for %s", temperature, context.serial)
                    return True
                if time.monotonic() - lastLogged >= cls.COOLDOWN_POLL_SECONDS:
                    log.info("[brake] waiting for bed cooldown (%.1f°C) on %s", temperature, context.serial)
                    lastLogged = time.monotonic()
            if time.monotonic() >= deadline:
                return False
            time.sleep(cls.COOLDOWN_POLL_SECONDS)

    @classmethod
    def _homeXY(cls, printer: Any, context: BrakeFlowContext) -> None:
        methodNames = [
            "home_xy",
            "homeXY",
            "go_home_xy",
            "home",
        ]
        for methodName in methodNames:
            method = getattr(printer, methodName, None)
            if callable(method):
                try:
                    method()
                    log.info("[brake] invoked %s for %s", methodName, context.serial)
                    return
                except Exception as error:
                    log.debug("[brake] %s failed for %s: %s", methodName, context.serial, error)
        gcodeSender = getattr(printer, "send_gcode", None)
        if callable(gcodeSender):
            try:
                gcodeSender("G28 X Y")
                log.info("[brake] issued G28 X Y for %s", context.serial)
            except Exception as error:
                log.warning("[brake] failed to issue G28 X Y for %s: %s", context.serial, error)
        else:
            log.warning("[brake] no XY homing method available for %s", context.serial)

    @classmethod
    def _performInspection(
        cls,
        printer: Any,
        context: BrakeFlowContext,
        captureFunc: SnapshotCaptureFunc,
        attempt: int,
    ) -> bool:
        inspectionDir = cls._prepareInspectionDirectory(context, attempt)
        percents = context.resolvedCheckpointPercents(cls.DEFAULT_CHECKPOINTS)
        for percent in percents:
            if cls.INSPECTION_SNAPSHOT_DELAY_SECONDS > 0:
                time.sleep(cls.INSPECTION_SNAPSHOT_DELAY_SECONDS)
            try:
                snapshotPath = captureFunc(printer, context.serial)
            except Exception as error:
                log.warning(
                    "[brake] failed to capture inspection snapshot for %s at %d%%: %s",
                    context.serial,
                    percent,
                    error,
                )
                return False
            targetPath = inspectionDir / f"pct_{percent:03d}.jpg"
            try:
                shutil.copy2(snapshotPath, targetPath)
            except Exception as error:
                log.debug("[brake] unable to copy snapshot to %s: %s", targetPath, error)
                targetPath = snapshotPath
            if not compareToReference(context.serial, targetPath, ref_index_hint=percent):
                log.warning(
                    "[inspect] obstruction suspected on %s (pct=%d attempt=%d)",
                    context.serial,
                    percent,
                    attempt,
                )
                # TODO: Issue brake G-code to clear the plate once motion safety is validated.
                return False
        return True

    @classmethod
    def _prepareInspectionDirectory(cls, context: BrakeFlowContext, attempt: int) -> Path:
        baseDirectory = Path.home() / ".printmaster" / "bed-checkpoints" / context.serial / context.normalizedJobKey()
        attemptDirectory = baseDirectory / f"inspect_attempt_{attempt:02d}"
        attemptDirectory.mkdir(parents=True, exist_ok=True)
        return attemptDirectory

    @classmethod
    def _normalizePercent(cls, value: Any) -> Optional[int]:
        try:
            percentFloat = float(value)
        except (TypeError, ValueError):
            return None
        percentInt = int(round(percentFloat))
        return max(0, min(100, percentInt))

    @classmethod
    def _readBedTemperature(cls, printer: Any) -> Optional[float]:
        stateAccessors = ["get_state", "get_current_state"]
        for accessorName in stateAccessors:
            accessor = getattr(printer, accessorName, None)
            if not callable(accessor):
                continue
            try:
                payload = accessor()
            except Exception as error:
                log.debug("[brake] %s failed: %s", accessorName, error)
                continue
            temperature = cls._searchTemperature(payload)
            if temperature is not None:
                return temperature
        return None

    @classmethod
    def _searchTemperature(cls, payload: Any) -> Optional[float]:
        if isinstance(payload, dict):
            for key, value in payload.items():
                normalizedKey = cls._normalizeKey(key)
                if normalizedKey in {"bedtemper", "bedtemperature", "bedtemp"}:
                    numeric = cls._coerceFloat(value)
                    if numeric is not None:
                        return numeric
                nested = cls._searchTemperature(value)
                if nested is not None:
                    return nested
        elif isinstance(payload, (list, tuple)):
            for item in payload:
                nested = cls._searchTemperature(item)
                if nested is not None:
                    return nested
        return None

    @staticmethod
    def _normalizeKey(value: Any) -> str:
        return "".join(character for character in str(value).lower() if character.isalnum())

    @staticmethod
    def _coerceFloat(value: Any) -> Optional[float]:
        if isinstance(value, bool):
            return float(value)
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            try:
                return float(stripped)
            except ValueError:
                return None
        return None


def buildBrakeFlowErrorPayload(context: BrakeFlowContext) -> Dict[str, Any]:
    return {
        "printerSerial": context.serial,
        "printerIpAddress": context.ipAddress,
        "errorCode": "PLATE_OBSTRUCTED",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "reason": "Brake-plate demo indicates leftover object",
        "jobKey": context.normalizedJobKey(),
    }


