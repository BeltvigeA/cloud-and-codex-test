"""Command execution helpers for Bambu printer control."""

from __future__ import annotations

import logging
from typing import Any, Dict

from .bambuClient import BambuLanClient

LOG = logging.getLogger(__name__)


def executeCommand(client: BambuLanClient, command: Dict[str, Any]) -> None:
    """Execute a queued printer command on the provided LAN client."""

    commandType = str(command.get("commandType") or "").strip().lower()
    metadata = command.get("metadata") or {}

    LOG.info("Executing command %s for %s", commandType or "<unknown>", client.ipAddress)

    if commandType == "set_bed_temp":
        target = int(metadata.get("target"))
        client.setBedTemp(target)
    elif commandType == "set_nozzle_temp":
        target = int(metadata.get("target"))
        client.setNozzleTemp(target)
    elif commandType == "home_all":
        client.homeAll()
    elif commandType == "jog":
        axis = str(metadata.get("axis"))
        delta = float(metadata.get("delta"))
        feed = int(metadata.get("feed", 3000))
        client.jog(axis, delta, feed)
    elif commandType == "camera_on":
        client.cameraOn()
    elif commandType == "camera_off":
        client.cameraOff()
    elif commandType == "poke":
        raise NotImplementedError("poke should be handled by status routines")
    else:
        raise ValueError(f"Unknown commandType {commandType}")


__all__ = ["executeCommand"]
