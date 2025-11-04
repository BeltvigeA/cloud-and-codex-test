from __future__ import annotations

import contextlib
import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, List, Optional

log = logging.getLogger(__name__)

# Signatur samsvarer med annen bruk i prosjektet
SnapshotCaptureFunc = Callable[[Any, str], Path]


def _reference_dir(serial: str) -> Path:
    target = Path.home() / ".printmaster" / "bed-reference" / serial.strip()
    target.mkdir(parents=True, exist_ok=True)
    return target


def storeBedReferenceFrame(serial: str, index: int, sourcePath: Path) -> Path:
    """
    Lagrer som ~/.printmaster/bed-reference/<serial>/frame_{index:03d}.jpg
    """
    destDir = _reference_dir(serial)
    dest = destDir / f"frame_{int(index):03d}.jpg"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(sourcePath), dest)
    log.info("[bedref] saved %s", dest)
    return dest


def _normalize_state(payload: Any) -> str:
    # Robust "best-effort" normalisering av tilstandsnavn fra bambulabs_api
    if payload is None:
        return ""
    if isinstance(payload, dict):
        for key in (
            "motion_state", "motionstate", "gcode_state", "gcodestate",
            "status", "current_state", "state",
        ):
            text = str(payload.get(key) or "").strip().lower()
            if text:
                return text
    if isinstance(payload, str):
        return payload.strip().lower()
    return ""


def _is_moving(state: Any) -> Optional[bool]:
    s = _normalize_state(state)
    if not s:
        return None
    if any(tok in s for tok in ("printing", "running", "busy", "moving", "homing", "processing")):
        return True
    if any(tok in s for tok in ("idle", "stopped", "completed", "finish", "finished", "paused", "pause")):
        return False
    return None


def _wait_for_motion(apiPrinter: Any, *, target: bool, timeout: float, poll: float) -> bool:
    deadline = time.monotonic() + max(timeout, 0.0)
    while time.monotonic() < deadline:
        try:
            state = apiPrinter.get_state()
        except Exception:
            time.sleep(poll)
            continue
        moving = _is_moving(state)
        if moving is None:
            time.sleep(poll)
            continue
        if moving == target:
            return True
        time.sleep(poll)
    return False


def capture_during_pauses(
    apiPrinter: Any,
    serial: str,
    captureFunc: SnapshotCaptureFunc,
    frames: int,
    *,
    settle: float = 0.15,
    poll: float = 0.15,
    timeout: float = 900.0,
) -> List[Path]:
    """
    Tar ett bilde hver gang maskinen stopper (G4-dwell). Vi legger oss
    litt ut i pausen (settle) for å treffe midt i 3s-vinduet.
    """
    results: List[Path] = []
    _wait_for_motion(apiPrinter, target=True, timeout=60.0, poll=poll)  # best-effort
    deadline = time.monotonic() + max(timeout, 0.0)
    while len(results) < max(1, frames) and time.monotonic() < deadline:
        if not _wait_for_motion(apiPrinter, target=False, timeout=30.0, poll=poll):
            log.info("[bedref] no further pause detected – stop")
            break
        time.sleep(max(0.0, settle))
        shot = captureFunc(apiPrinter, serial)  # skal gi Path til midlertidig snapshot
        saved = storeBedReferenceFrame(serial, len(results) + 1, shot)
        results.append(saved)
        if len(results) >= frames:
            break
        if not _wait_for_motion(apiPrinter, target=True, timeout=30.0, poll=poll):
            log.info("[bedref] no further movement – stop")
            break
    return results


def resolve_bedref_3mf_path() -> Path:
    # Søk både i klientens assets/ og i brukerens .printmaster/files/
    candidates = [
        Path(__file__).resolve().parent.parent / "assets" / "bedRefCaputre.gcode.3mf",
        Path.home() / ".printmaster" / "files" / "bedRefCaputre.gcode.3mf",
    ]
    for c in candidates:
        if c.exists():
            return c
    raise FileNotFoundError("bedRefCaputre.gcode.3mf ikke funnet i client/assets/ eller ~/.printmaster/files/")


def run_bed_reference_capture(
    *,
    ip: str,
    serial: str,
    accessCode: str,
    captureFunc: SnapshotCaptureFunc,
    frames: int,
    optionsFactory: Callable[..., Any],   # BambuPrintOptions
    sendJobFunc: Callable[..., dict],     # sendBambuPrintJob
    bambuApiModule: Any,                  # bambulabs_api modul (eksponerer Printer-klassen)
) -> List[Path]:
    """
    Starter .3mf-jobben og snapshoter i hver 3s-pause til 'frames' bilder er lagret.
    """
    three_mf = resolve_bedref_3mf_path()
    options = optionsFactory(
        ipAddress=ip,
        serialNumber=serial,
        accessCode=accessCode,
        startStrategy="api",
        enableTimeLapse=False,
    )
    sendJobFunc(filePath=three_mf, options=options)

    # Opprett en overvåknings-instans for state + snapshot
    printer = getattr(bambuApiModule, "Printer")(ip, accessCode, serial)
    try:
        if hasattr(printer, "mqtt_start"):
            printer.mqtt_start()
        elif hasattr(printer, "connect"):
            printer.connect()
    except Exception:
        pass
    try:
        return capture_during_pauses(printer, serial, captureFunc, frames)
    finally:
        for meth in ("disconnect", "mqtt_stop"):
            fn = getattr(printer, meth, None)
            if callable(fn):
                with contextlib.suppress(Exception):
                    fn()
