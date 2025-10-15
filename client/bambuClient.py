"""LAN client helpers for interacting with Bambu printers."""

from __future__ import annotations

import logging
import socket
from typing import Any, Optional

from .logbus import log

try:  # pragma: no cover - optional dependency in some environments
    import bambulabs_api as bambuApi  # type: ignore
except ImportError:  # pragma: no cover - handled gracefully by callers
    bambuApi = None  # type: ignore

LOG = logging.getLogger(__name__)

HEALTH_TIMEOUT_SECONDS = 2.0


class BambuLanClient:
    """High-level LAN client with safety checks for Bambu printers."""

    def __init__(
        self,
        ipAddress: str,
        accessCode: str,
        serialNumber: Optional[str] = None,
        *,
        connectCamera: bool = False,
    ) -> None:
        self.ipAddress = ipAddress
        self.accessCode = accessCode
        self.serialNumber = serialNumber
        self.connectCamera = connectCamera
        self.printer: Any | None = None

    def connect(self) -> Any:
        """Establish an MQTT (and optionally camera) session."""

        if bambuApi is None:
            raise RuntimeError("bambulabs_api is required for LAN control")

        printerClass = getattr(bambuApi, "Printer", None)
        if printerClass is None:
            raise RuntimeError("bambulabs_api.Printer is unavailable")

        self.printer = printerClass(self.ipAddress, self.accessCode, self.serialNumber)
        if self.connectCamera and hasattr(self.printer, "connect"):
            self.printer.connect()
        else:
            mqttStart = getattr(self.printer, "mqtt_start", None)
            if callable(mqttStart):
                mqttStart()
            elif hasattr(self.printer, "connect"):
                self.printer.connect()
        return self.printer

    def disconnect(self) -> None:
        """Cleanly close the underlying printer connection."""

        if not self.printer:
            return
        disconnectMethod = getattr(self.printer, "disconnect", None)
        if callable(disconnectMethod):
            try:
                disconnectMethod()
            except Exception:  # pragma: no cover - best effort cleanup
                LOG.debug("Printer disconnect failed", exc_info=True)
        mqttStop = getattr(self.printer, "mqtt_stop", None)
        if callable(mqttStop):
            try:
                mqttStop()
            except Exception:  # pragma: no cover - best effort cleanup
                LOG.debug("Printer mqtt_stop failed", exc_info=True)

    def healthCheck(self) -> bool:
        """Return True when the MQTT port responds within the timeout."""

        try:
            with socket.create_connection((self.ipAddress, 8883), timeout=HEALTH_TIMEOUT_SECONDS):
                return True
        except OSError:
            return False

    def ensureConnectedAndHealthy(self) -> bool:
        """Connect on demand and verify that the printer is reachable."""

        if self.printer is None:
            try:
                self.connect()
            except Exception:  # pragma: no cover - connection errors handled via False
                LOG.debug("Unable to connect printer", exc_info=True)
                log("ERROR", "status-printer", "mqtt_connect", ip=self.ipAddress, ok=False)
                return False
        log("INFO", "status-printer", "mqtt_connect", ip=self.ipAddress, ok=self.printer is not None)
        healthy = self.healthCheck()
        log("INFO", "status-printer", "mqtt_port_probe", ip=self.ipAddress, ok=healthy)
        return healthy

    def _sendGcode(self, command: str) -> None:
        if not self.printer:
            raise RuntimeError("Printer connection missing")
        sendMethod = getattr(self.printer, "send_gcode", None)
        if not callable(sendMethod):
            raise RuntimeError("This bambulabs_api version lacks send_gcode support")
        log("INFO", "control", "gcode", line=command)
        sendMethod(command)

    def setBedTemp(self, targetCelsius: int) -> None:
        if not self.ensureConnectedAndHealthy():
            raise RuntimeError("Printer offline")
        if hasattr(self.printer, "set_bed_temperature"):
            self.printer.set_bed_temperature(int(targetCelsius))
        else:
            self._sendGcode(f"M140 S{int(targetCelsius)}")

    def setNozzleTemp(self, targetCelsius: int) -> None:
        if not self.ensureConnectedAndHealthy():
            raise RuntimeError("Printer offline")
        if hasattr(self.printer, "set_nozzle_temperature"):
            self.printer.set_nozzle_temperature(int(targetCelsius))
        else:
            self._sendGcode(f"M104 S{int(targetCelsius)}")

    def homeAll(self) -> None:
        if not self.ensureConnectedAndHealthy():
            raise RuntimeError("Printer offline")
        if hasattr(self.printer, "home"):
            self.printer.home()
        else:
            self._sendGcode("G28")

    def jog(self, axis: str, deltaMillimeters: float, feedMillimetersPerMinute: int = 3000) -> None:
        if not self.ensureConnectedAndHealthy():
            raise RuntimeError("Printer offline")
        normalizedAxis = axis.upper()
        if normalizedAxis not in {"X", "Y", "Z"}:
            raise ValueError("axis must be X, Y or Z")
        safeFeed = int(feedMillimetersPerMinute)
        self._sendGcode("G91")
        if normalizedAxis == "X":
            self._sendGcode(f"G1 X{deltaMillimeters} F{safeFeed}")
        elif normalizedAxis == "Y":
            self._sendGcode(f"G1 Y{deltaMillimeters} F{safeFeed}")
        else:
            zFeed = min(1200, safeFeed)
            self._sendGcode(f"G1 Z{deltaMillimeters} F{zFeed}")
        self._sendGcode("G90")

    def cameraOn(self) -> None:
        if not self.ensureConnectedAndHealthy():
            raise RuntimeError("Printer offline")
        connectMethod = getattr(self.printer, "connect", None)
        if callable(connectMethod):
            connectMethod()

    def cameraOff(self) -> None:
        if not self.printer:
            return
        try:
            self.disconnect()
        finally:
            try:
                if bambuApi is None:
                    return
                printerClass = getattr(bambuApi, "Printer", None)
                if printerClass is None:
                    return
                self.printer = printerClass(self.ipAddress, self.accessCode, self.serialNumber)
                mqttStart = getattr(self.printer, "mqtt_start", None)
                if callable(mqttStart):
                    mqttStart()
            except Exception:  # pragma: no cover - best effort reconnect without camera
                LOG.debug("Failed to restart MQTT after cameraOff", exc_info=True)


__all__ = ["BambuLanClient", "HEALTH_TIMEOUT_SECONDS"]
