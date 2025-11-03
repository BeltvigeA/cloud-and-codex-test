from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import Any, Callable, List, Sequence

log = logging.getLogger(__name__)

SnapshotCaptureFunc = Callable[[Any, str], Path]

DEFAULT_REFERENCE_FRAMES = 47
DEFAULT_REFERENCE_DELAY_SECONDS = 0.2
_GCODE_HOME_SEQUENCE = ("G28",)
_GCODE_HEAD_PARK_SEQUENCE = ("G90", "G1 X0 Y250 F6000")
_GCODE_LOWER_SEQUENCE = ("G91", "G1 Z5 F600", "G90")
_MOVEMENT_DISTANCE_MM = 5.0
_MOVEMENT_FEEDRATE_MM_PER_MINUTE = 600.0
_PARK_POSITION_X_MM = 0.0
_PARK_POSITION_Y_MM = 250.0
_PARK_FEEDRATE_MM_PER_MINUTE = 6000.0
_MOVEMENT_SETTLE_SECONDS = _MOVEMENT_DISTANCE_MM / (
    _MOVEMENT_FEEDRATE_MM_PER_MINUTE / 60.0
)
_HOME_AND_PARK_SETTLE_SECONDS = 2.0
_MOTION_WAIT_TIMEOUT_SECONDS = 45.0
_MOTION_WAIT_POLL_SECONDS = 0.25


def _callPrinterMethod(printer: Any, methodNames: Sequence[str], *args: Any, **kwargs: Any) -> str | None:
    for methodName in methodNames:
        method = getattr(printer, methodName, None)
        if not callable(method):
            continue
        try:
            method(*args, **kwargs)
            return methodName
        except Exception as error:
            log.debug("[ref] %s failed via %s: %s", methodName, type(printer).__name__, error)
    return None


def _readMotionState(printer: Any, accessors: Sequence[str]) -> str:
    for accessorName in accessors:
        attribute = getattr(printer, accessorName, None)
        value: Any
        if callable(attribute):
            try:
                value = attribute()
            except Exception:
                continue
        elif attribute is None:
            continue
        else:
            value = attribute
        text = _normalizeState(value)
        if text:
            return text
    return ""


def _normalizeState(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in (
            "motion_state",
            "state",
            "gcode_state",
            "job_state",
            "sub_state",
            "status",
            "message",
        ):
            if key in value:
                nested = _normalizeState(value[key])
                if nested:
                    return nested
        return ""
    text = str(value).strip().lower()
    return text


def _isActiveMotionState(stateText: str) -> bool:
    if not stateText:
        return False
    lowered = stateText.lower()
    activeKeywords = ("hom", "mov", "busy", "start", "prepare", "work", "run")
    return any(keyword in lowered for keyword in activeKeywords)


def _waitForMotionCompletion(printer: Any, description: str) -> bool:
    waitMethodNames = (
        "wait_for_motion_idle",
        "wait_for_motion_complete",
        "wait_for_idle",
        "wait_until_idle",
        "waitForIdle",
        "wait_for_ready",
    )
    for methodName in waitMethodNames:
        method = getattr(printer, methodName, None)
        if not callable(method):
            continue
        try:
            method(timeout=_MOTION_WAIT_TIMEOUT_SECONDS)
            return True
        except TypeError:
            method()  # type: ignore[misc]
            return True
        except Exception as error:
            log.debug("[ref] %s wait via %s failed: %s", description, methodName, error)
    accessorNames = (
        "get_state",
        "get_current_state",
        "state",
        "current_state",
    )
    availableAccessors = [name for name in accessorNames if getattr(printer, name, None) is not None]
    if not availableAccessors:
        return False
    deadline = time.monotonic() + _MOTION_WAIT_TIMEOUT_SECONDS
    lastState = ""
    while time.monotonic() < deadline:
        stateText = _readMotionState(printer, availableAccessors)
        if stateText:
            if stateText != lastState:
                log.debug("[ref] %s state=%s", description, stateText)
                lastState = stateText
            if not _isActiveMotionState(stateText):
                return True
        time.sleep(_MOTION_WAIT_POLL_SECONDS)
    return False


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
    controlSenders: List[Callable[[dict], Any]] = []
    for attrName in ("send_control", "send_request"):
        candidate = getattr(printer, attrName, None)
        if callable(candidate):
            controlSenders.append(candidate)

    def sendControlPayload(payload: dict, action: str) -> bool:
        nonlocal controlSenders
        for sender in list(controlSenders):
            senderName = getattr(sender, "__name__", repr(sender))
            try:
                sender(payload)
                log.info("[ref] %s via %s for %s", action, senderName, sanitizedSerial)
                return True
            except Exception as error:
                log.warning(
                    "[ref] %s failed via %s for %s: %s",
                    action,
                    senderName,
                    sanitizedSerial,
                    error,
                )
                controlSenders.remove(sender)
        return False

    def sendGcodeSequence(commands: Sequence[str], action: str) -> bool:
        nonlocal sendGcodeFunc
        if sendGcodeFunc is None:
            return False
        try:
            for gcodeCommand in commands:
                sendGcodeFunc(gcodeCommand)
            log.info("[ref] %s via send_gcode for %s", action, sanitizedSerial)
            return True
        except Exception as error:
            log.warning(
                "[ref] %s failed via send_gcode for %s: %s",
                action,
                sanitizedSerial,
                error,
            )
            sendGcodeFunc = None
            return False

    preCaptureDelaySeconds = delayValue
    homeTransport: str | None = None
    homeMethod = _callPrinterMethod(printer, ["home_all", "homeAll", "home"])
    if homeMethod:
        log.info("[ref] invoked %s for %s", homeMethod, sanitizedSerial)
        homeTransport = "method"
    elif sendControlPayload({"motion": {"command": "home_all"}}, "home_all"):
        homeTransport = "control"
    elif sendGcodeSequence(_GCODE_HOME_SEQUENCE, "home_all"):
        homeTransport = "gcode"
    elif sendGcodeFunc is None and not controlSenders:
        log.warning(
            "[ref] unable to home or park print head for %s: no supported motion transport",
            sanitizedSerial,
        )

    if homeTransport is not None:
        if not _waitForMotionCompletion(printer, "home_all"):
            log.debug("[ref] motion completion wait skipped or timed out for %s", sanitizedSerial)

    parkTransport: str | None = None
    if homeTransport is not None or controlSenders or sendGcodeFunc is not None:
        parkMethod = _callPrinterMethod(
            printer,
            [
                "park_head",
                "parkHead",
                "park_nozzle",
                "parkNozzle",
                "move_to_parking",
                "moveToParking",
            ],
        )
        if parkMethod:
            log.info("[ref] invoked %s for %s", parkMethod, sanitizedSerial)
            parkTransport = "method"
        else:
            for candidateName in ("move_to", "moveTo", "goto", "goTo"):
                candidate = getattr(printer, candidateName, None)
                if not callable(candidate):
                    continue
                try:
                    candidate(
                        x=_PARK_POSITION_X_MM,
                        y=_PARK_POSITION_Y_MM,
                        feedrate=int(_PARK_FEEDRATE_MM_PER_MINUTE),
                    )
                    log.info("[ref] invoked %s(x=%.1f,y=%.1f) for %s", candidateName, _PARK_POSITION_X_MM, _PARK_POSITION_Y_MM, sanitizedSerial)
                    parkTransport = "method"
                    break
                except TypeError:
                    try:
                        candidate(_PARK_POSITION_X_MM, _PARK_POSITION_Y_MM)
                        log.info(
                            "[ref] invoked %s(%.1f, %.1f) for %s",
                            candidateName,
                            _PARK_POSITION_X_MM,
                            _PARK_POSITION_Y_MM,
                            sanitizedSerial,
                        )
                        parkTransport = "method"
                        break
                    except Exception as error:
                        log.debug("[ref] %s positional invocation failed for %s: %s", candidateName, sanitizedSerial, error)
                except Exception as error:
                    log.debug("[ref] %s failed for %s: %s", candidateName, sanitizedSerial, error)
            if parkTransport is None:
                parkPayload = {
                    "motion": {
                        "command": "move",
                        "mode": "absolute",
                        "position": {
                            "x": float(_PARK_POSITION_X_MM),
                            "y": float(_PARK_POSITION_Y_MM),
                        },
                        "feedrate": int(_PARK_FEEDRATE_MM_PER_MINUTE),
                    }
                }
                if sendControlPayload(parkPayload, "park head"):
                    parkTransport = "control"
                elif sendGcodeSequence(_GCODE_HEAD_PARK_SEQUENCE, "park head"):
                    parkTransport = "gcode"
                elif homeTransport is not None:
                    log.warning("[ref] unable to park print head for %s", sanitizedSerial)

    if parkTransport is not None:
        if not _waitForMotionCompletion(printer, "park head"):
            log.debug("[ref] park wait skipped or timed out for %s", sanitizedSerial)

    if homeTransport is not None or parkTransport is not None:
        preCaptureDelaySeconds = max(delayValue, _HOME_AND_PARK_SETTLE_SECONDS)

    if preCaptureDelaySeconds > 0.0:
        time.sleep(preCaptureDelaySeconds)
    movementRequired = normalizedFrameCount > 1
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
            if movementRequired:
                lowered = False
                moveMethod = getattr(printer, "move", None)
                if callable(moveMethod):
                    try:
                        moveMethod("z", -float(_MOVEMENT_DISTANCE_MM), int(_MOVEMENT_FEEDRATE_MM_PER_MINUTE))
                        log.info("[ref] move('z', -%.1f) invoked for %s", _MOVEMENT_DISTANCE_MM, sanitizedSerial)
                        lowered = True
                    except Exception as error:
                        log.debug("[ref] move('z') failed for %s: %s", sanitizedSerial, error)
                if not lowered:
                    lowerPayload = {
                        "motion": {
                            "command": "move",
                            "axis": "z",
                            "distance": -float(_MOVEMENT_DISTANCE_MM),
                            "feedrate": int(_MOVEMENT_FEEDRATE_MM_PER_MINUTE),
                        }
                    }
                    if sendControlPayload(lowerPayload, "lower build plate"):
                        lowered = True
                if not lowered and sendGcodeSequence(_GCODE_LOWER_SEQUENCE, "lower build plate"):
                    lowered = True
                if lowered:
                    if not _waitForMotionCompletion(printer, "lower build plate"):
                        log.debug("[ref] lower wait skipped or timed out for %s", sanitizedSerial)
                    frameDelaySeconds = max(delayValue, _MOVEMENT_SETTLE_SECONDS)
                else:
                    log.warning(
                        "[ref] unable to lower build plate for %s: no supported motion control",
                        sanitizedSerial,
                    )
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

