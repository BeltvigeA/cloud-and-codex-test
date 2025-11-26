from __future__ import annotations
import os

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


# --- Z-axis reference capture using bambulabs_api.Printer.move_z_axis (API ONLY) ---
from .bedref_capture import storeBedReferenceFrame


# ------- NYTT: eksakt tilkoblings- og homelogikk (som i home_printer.py) -------
def _mqtt_ready(printer) -> bool:
    try:
        fn = getattr(printer, "mqtt_client_ready", None)
        if callable(fn) and fn():
            return True
    except Exception:
        pass
    try:
        fn = getattr(printer, "is_connected", None)
        if callable(fn) and fn():
            return True
    except Exception:
        pass
    try:
        client = getattr(printer, "mqtt_client", None)
        if client and getattr(client, "is_connected", None):
            return bool(client.is_connected())
    except Exception:
        pass
    return False

def _connect_and_wait(printer, retries: int = 3, connect_timeout_s: float = 15.0, retry_delay_s: float = 1.5) -> None:
    for attempt in range(1, retries + 1):
        try:
            printer.connect()
        except Exception as ex:  # best effort, prøv videre
            log.debug("connect() attempt %d/%d raised: %s", attempt, retries, ex)
        t0 = time.time()
        while time.time() - t0 < connect_timeout_s:
            if _mqtt_ready(printer):
                return
            time.sleep(0.4)
        log.debug("Not connected (attempt %d). Retrying in %.1fs ...", attempt, retry_delay_s)
        try:
            d = getattr(printer, "disconnect", None)
            if callable(d):
                d()
        except Exception:
            pass
        time.sleep(retry_delay_s)
    raise RuntimeError("Could not establish MQTT session with the printer")

def _home_printer_exact(printer, serial: str = "") -> bool:
    """Eksakt Bambu-SDK-kall, samme flyt som i enkeltskripta: home_printer() -> bool."""
    printer_id = serial or "unknown"
    home_fn = getattr(printer, "home_printer", None)
    if not callable(home_fn):
        log.warning("home_printer() not available on printer %s", printer_id)
        return False
    try:
        ok = bool(home_fn())
        log.info("home_printer() returned: %s", ok)
        return ok
    except Exception as ex:
        log.debug("home_printer() raised: %s", ex)
        return False
# -------------------------------------------------------------------------------

def _awaitPrinterReady(printer: Any, *, timeout: float = 12.0, poll: float = 0.25) -> bool:
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        try:
            state = printer.get_state()
            if state:
                return True
        except Exception:
            pass
        time.sleep(poll)
    return False

def _ensure_connected_and_mqtt(printer: Any, *, timeout: float = 12.0) -> None:
    """
    Streng tilkobling før bevegelses-API:
      - connect()
      - mqtt_start() og vent på mqtt_client_ready() når tilgjengelig
    """
    # Koble til
    connect = getattr(printer, "connect", None)
    if callable(connect):
        try:
            connect()
        except Exception as e:
            raise RuntimeError(f"Unable to connect to printer: {e}") from e

    # Start MQTT og vent (best-effort)
    mqtt_start = getattr(printer, "mqtt_start", None)
    mqtt_ready = getattr(printer, "mqtt_client_ready", None)
    if callable(mqtt_start):
        deadline = time.monotonic() + max(2.0, min(10.0, timeout))
        last_err: Exception | None = None
        while True:
            try:
                mqtt_start()
            except Exception as e:
                last_err = e
                break
            if not callable(mqtt_ready):
                break
            try:
                if mqtt_ready():
                    break
            except Exception as e:
                last_err = e
            if time.monotonic() >= deadline:
                break
            time.sleep(0.25)
        # Ikke fatal hvis MQTT ikke blir klar, men vi forsøker å hente state under.
        if last_err:
            # kun debug – ikke raise, vi håndterer senere
            pass


def _safe_wait_motion(printer: Any, label: str, fallback_seconds: float = 2.0) -> None:
    """
    Vent på at bevegelsen er ferdig. Forsøk modulens _waitForMotionCompletion om den finnes,
    ellers sov et konservativt antall sekunder. Dette gjør oss robuste dersom MQTT/state ikke er tilgjengelig.
    """
    _wait_func = None
    try:
        # Importer hvis tilgjengelig i denne klienten
        from .bedref_capture import _waitForMotionCompletion as _wait_func  # type: ignore
    except Exception:
        _wait_func = None
    if _wait_func:
        try:
            _wait_func(printer, label)  # kan bruke printer.get_state() internt
            return
        except Exception:
            pass
    time.sleep(max(0.0, float(fallback_seconds)))

def captureZAxisReferenceSequence(
    printer: Any,
    serial: str,
    captureFunc: SnapshotCaptureFunc,
    *,
    step_mm: float = 5.0,
    total_mm: float = 200.0,
    delaySeconds: float = DEFAULT_REFERENCE_DELAY_SECONDS,
    home_first: bool = True,
    limit_frames: int | None = None,
) -> List[Path]:
    """
    Home → bilde @Z≈0 → flytt i absolutte Z-steg og ta bilde for hvert steg.
    Hvis 'limit_frames' (eller env BEDREF_FRAMES) settes, velges effektivt steg som total_mm/limit_frames
    slik at du får nøyaktig limit_frames+1 bilder (inkludert Z=0).
    """
    sanitizedSerial = str(serial or "").strip()
    if not sanitizedSerial:
        raise ValueError("Serial number is required for Z-axis reference capture")

    # Robust tilkobling før homing og Z-bevegelse
    # 1) Koble til og vent faktisk MQTT-ready (som i home_printer.py)
    _connect_and_wait(printer, retries=3, connect_timeout_s=15.0, retry_delay_s=1.5)

    moveZ = getattr(printer, "move_z_axis", None)
    if not callable(moveZ):
        raise RuntimeError("bambulabs_api.Printer.move_z_axis is not available on this printer object")

    # Home først med EKSPLISITT API: home_printer() → bool, så Z=0 fallback via move_z_axis(0)
    if home_first:
        if not _home_printer_exact(printer, serial):
            raise RuntimeError("Unable to home printer via API method home_printer()")
        # liten settle uansett
        time.sleep(3.0)

    # Målmappe

    # Antall rammer og effektivt steg
    frames_env = os.getenv("BEDREF_FRAMES")
    if limit_frames is None:
        if frames_env:
            try:
                limit_frames = int(frames_env)
            except ValueError:
                limit_frames = None
        else:
            limit_frames = 40  # standard: 40 steg → ~41 bilder inkl. Z=0
    if limit_frames and limit_frames > 0:
        effective_step = float(total_mm) / float(limit_frames)
    else:
        effective_step = max(0.1, float(step_mm))

    # Første ramme @Z≈0
    capturedPaths: List[Path] = []
    firstTmp = captureFunc(printer, sanitizedSerial)
    firstOut = storeBedReferenceFrame(sanitizedSerial, 1, firstTmp)  # frame_001.jpg
    capturedPaths.append(firstOut)

    # Steg i absolutte høyder
    steps = int(round(float(total_mm) / effective_step))
    for i in range(1, steps + 1):
        height = int(round(i * effective_step))
        ok = False
        try:
            result = moveZ(height)
            ok = bool(result)
        except Exception as ex:
            log.error("[ref] move_z_axis(%d) raised: %s", height, ex)
            raise
        if not ok:
            raise RuntimeError(f"move_z_axis({height}) returned False/failed")

        _safe_wait_motion(printer, f"move_z_axis({height})", fallback_seconds=2.0)
        if delaySeconds > 0.0:
            time.sleep(max(0.0, float(delaySeconds)))
        tmp = captureFunc(printer, sanitizedSerial)
        out = storeBedReferenceFrame(sanitizedSerial, len(capturedPaths) + 1, tmp)
        capturedPaths.append(out)

    log.info(
        "[ref] completed Z-axis reference capture for %s (step=%.3f, total=%.1f) -> %d frame(s)",
        sanitizedSerial,
        effective_step,
        float(total_mm),
        len(capturedPaths),
    )
    return capturedPaths


