from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import Any, Callable, List

log = logging.getLogger(__name__)

SnapshotCaptureFunc = Callable[[Any, str], Path]

DEFAULT_REFERENCE_FRAMES = 47
DEFAULT_REFERENCE_DELAY_SECONDS = 0.2
_GCODE_HOME_SEQUENCE = ("G28",)
_GCODE_HEAD_PARK_SEQUENCE = ("G90", "G1 X0 Y250 F6000")
_GCODE_LOWER_SEQUENCE = ("G91", "G1 Z5 F600", "G90")
_MOVEMENT_DISTANCE_MM = 5.0
_MOVEMENT_FEEDRATE_MM_PER_MINUTE = 600.0
_MOVEMENT_SETTLE_SECONDS = _MOVEMENT_DISTANCE_MM / (
    _MOVEMENT_FEEDRATE_MM_PER_MINUTE / 60.0
)
_HOME_AND_PARK_SETTLE_SECONDS = 2.0


def _resolveSerialDirectory(serial: str) -> Path:
    serialDirectory = Path.home() / ".printmaster" / "bed-reference" / serial
    serialDirectory.mkdir(parents=True, exist_ok=True)
    return serialDirectory


def captureReferenceSequence(
    printer: Any,
    serial: str,
    captureFunc: SnapshotCaptureFunc,
    *,
    frameCount: int = DEFAULT_REFERENCE_FRAMES,
    delaySeconds: float = DEFAULT_REFERENCE_DELAY_SECONDS,
) -> List[Path]:
    sanitizedSerial = str(serial or "").strip()
    if not sanitizedSerial:
        raise ValueError("Serial number is required for reference capture")
    normalizedFrameCount = max(1, int(frameCount))
    delayValue = max(0.0, float(delaySeconds))
    referenceDirectory = _resolveSerialDirectory(sanitizedSerial)
    log.info(
        "[ref] capturing %d-frame reference for %s into %s",
        normalizedFrameCount,
        sanitizedSerial,
        referenceDirectory,
    )
    capturedPaths: List[Path] = []
    gcodeSender = getattr(printer, "send_gcode", None)
    sendGcodeFunc = gcodeSender if callable(gcodeSender) else None
    if sendGcodeFunc is None:
        log.warning(
            "[ref] unable to home or park print head for %s: printer has no send_gcode",
            sanitizedSerial,
        )
    else:
        preCaptureDelaySeconds = delayValue
        try:
            for gcodeCommand in _GCODE_HOME_SEQUENCE:
                sendGcodeFunc(gcodeCommand)
            for gcodeCommand in _GCODE_HEAD_PARK_SEQUENCE:
                sendGcodeFunc(gcodeCommand)
            preCaptureDelaySeconds = max(delayValue, _HOME_AND_PARK_SETTLE_SECONDS)
        except Exception as error:
            log.warning(
                "[ref] failed to prepare printer for %s: %s",
                sanitizedSerial,
                error,
            )
            sendGcodeFunc = None
            preCaptureDelaySeconds = delayValue
        if preCaptureDelaySeconds > 0.0:
            time.sleep(preCaptureDelaySeconds)
    movementRequired = normalizedFrameCount > 1
    if movementRequired and sendGcodeFunc is None:
        log.warning(
            "[ref] unable to lower build plate for %s: printer has no send_gcode",
            sanitizedSerial,
        )
    for index in range(normalizedFrameCount):
        log.debug(
            "[ref] capturing frame %d/%d for %s",
            index + 1,
            normalizedFrameCount,
            sanitizedSerial,
        )
        snapshotPath = captureFunc(printer, sanitizedSerial)
        targetPath = referenceDirectory / f"z_{index:03d}.jpg"
        shutil.copy2(snapshotPath, targetPath)
        capturedPaths.append(targetPath)
        if index + 1 < normalizedFrameCount:
            frameDelaySeconds = delayValue
            if sendGcodeFunc is not None:
                try:
                    for gcodeCommand in _GCODE_LOWER_SEQUENCE:
                        sendGcodeFunc(gcodeCommand)
                    frameDelaySeconds = max(delayValue, _MOVEMENT_SETTLE_SECONDS)
                except Exception as error:
                    log.warning(
                        "[ref] failed to lower build plate for %s: %s",
                        sanitizedSerial,
                        error,
                    )
                    sendGcodeFunc = None
                    frameDelaySeconds = delayValue
            if frameDelaySeconds > 0.0:
                time.sleep(frameDelaySeconds)
    log.info(
        "[ref] completed reference capture for %s (%d frame(s))",
        sanitizedSerial,
        len(capturedPaths),
    )
    return capturedPaths


def capture_reference_sequence(
    printer: Any,
    serial: str,
    captureFunc: SnapshotCaptureFunc,
    *,
    frameCount: int = DEFAULT_REFERENCE_FRAMES,
    delaySeconds: float = DEFAULT_REFERENCE_DELAY_SECONDS,
) -> List[Path]:
    return captureReferenceSequence(
        printer,
        serial,
        captureFunc,
        frameCount=frameCount,
        delaySeconds=delaySeconds,
    )

