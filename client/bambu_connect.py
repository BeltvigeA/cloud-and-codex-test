
from __future__ import annotations

import os
import sys
import subprocess
from pathlib import Path
from urllib.parse import quote

from .logbus import log

def _ensure_abs(path_like: str) -> str:
    p = Path(path_like).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"File not found: {p}")
    return str(p)

def build_connect_url(abs_path: str, display_name: str) -> str:
    return (
        "bambu-connect://import-file"
        f"?path={quote(abs_path, safe='')}"
        f"&name={quote(display_name or Path(abs_path).name, safe='')}"
        f"&version=1.0.0"
    )

def _open_url(url: str) -> None:
    if sys.platform.startswith("win"):
        os.startfile(url)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.run(["open", url], check=False)
    else:
        subprocess.run(["xdg-open", url], check=False)

def send_via_bambu_connect(local_path: str, display_name: str) -> bool:
    try:
        abs_path = _ensure_abs(local_path)
        url = build_connect_url(abs_path, display_name)
        log("INFO", "print-job", "connect_open_url", url=url, file=abs_path)
        _open_url(url)
        log("INFO", "print-job", "connect_launched", file=abs_path)
        return True
    except Exception as e:  # noqa: BLE001
        log("ERROR", "print-job", "connect_launch_failed", file=local_path, error=str(e))
        return False
