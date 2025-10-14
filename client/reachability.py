"""Network reachability checks for printer devices."""

from __future__ import annotations

import logging
import socket
from typing import Iterable

_LOG = logging.getLogger(__name__)

_DEFAULT_PORTS: tuple[int, ...] = (8883, 990)


def tcpCheck(host: str, *, ports: Iterable[int] = _DEFAULT_PORTS, timeoutSeconds: float = 1.5) -> bool:
    """Attempt to establish a TCP connection to any of the provided ports."""

    candidateHost = (host or "").strip()
    if not candidateHost:
        return False

    for port in tuple(ports):
        try:
            with socket.create_connection((candidateHost, int(port)), timeout=timeoutSeconds):
                return True
        except OSError as error:
            _LOG.debug("Reachability check failed for %s:%s: %s", candidateHost, port, error)
            continue
        except Exception as error:  # noqa: BLE001 - defensive logging
            _LOG.debug("Unexpected error probing %s:%s: %s", candidateHost, port, error)
            continue
    return False


__all__ = ["tcpCheck"]
