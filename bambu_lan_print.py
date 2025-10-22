#!/usr/bin/env python3
"""
Kompleks eksempel: Send .3mf eller .gcode til Bambu-skriver over LAN og start print via MQTT,
med valgfri sanntidsmonitor og fallback til Bambu Connect.

Utvidet versjon med robust MQTT-tilkobling, kontrollkommandoer og automatisk
oppdatering av LAN-konfigurasjon basert på jobbinformasjon.
"""
from __future__ import annotations

import argparse
import io
import json
import platform
import re
import ssl
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional

from rich import print as rprint
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

try:
    import bambulabs_api as bl
except ImportError as error:
    rprint("[bold red]Mangler 'bambulabs_api'. Kjør: pip install bambulabs_api[/bold red]")
    raise

CONFIG_PATH = Path.home() / ".printmaster" / "printers.json"


def loadPrinterMap() -> Dict[str, Dict[str, str]]:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def savePrinterMap(mapping: Dict[str, Dict[str, str]]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(mapping, indent=2), encoding="utf-8")


def updatePrinterFromJob(jobMetadata: Dict[str, Any]) -> tuple[str, str, str]:
    serial = jobMetadata.get("printer_serial") or jobMetadata.get("serial")
    ip = jobMetadata.get("printer_ip") or jobMetadata.get("ip")
    accessCode = jobMetadata.get("access_code") or jobMetadata.get("lan_access_code")

    if not serial:
        raise ValueError("Job metadata mangler printer_serial")

    mapping = loadPrinterMap()
    stored = mapping.get(serial, {})

    changed = False
    if ip and accessCode:
        changed = stored.get("ip") != ip or stored.get("access_code") != accessCode
        if changed:
            mapping[serial] = {"ip": ip, "access_code": accessCode}
            savePrinterMap(mapping)
            rprint(f"[green]Oppdatert lagret info for {serial}: ip={ip}, access_code=****[/green]")

    if not ip:
        ip = stored.get("ip")
    if not accessCode:
        accessCode = stored.get("access_code")

    if not ip or not accessCode:
        raise ValueError("IP eller access code mangler i metadata og lagret config")

    return serial, ip, accessCode


def sanitizeUploadName(uploadName: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_.-]+", "_", uploadName)
    if not sanitized.endswith(".3mf"):
        sanitized += ".3mf"
    return sanitized


class BambuLanClient:
    """Høynivå-klient som bruker bambulabs_api for MQTT+FTPS."""

    def __init__(self, ip: str, serial: str, accessCode: str, connectCamera: bool = False):
        self.ip = ip
        self.serial = serial
        self.accessCode = accessCode
        self.connectCamera = connectCamera
        self.printer: Optional[bl.Printer] = None

    def waitForMqttReady(self, timeout: float = 20.0, poll: float = 0.5) -> None:
        if not self.printer:
            raise RuntimeError("Ikke tilkoblet")

        startTime = time.time()
        lastState = None
        while time.time() - startTime < timeout:
            try:
                state = self.printer.get_state()
                percentage = self.printer.get_percentage()
                if state is not None and percentage is not None:
                    if lastState == state:
                        return
                    lastState = state
            except Exception:
                pass
            time.sleep(poll)
        raise TimeoutError("MQTT ble ikke klar innen tidsfristen")

    def connectPrinter(self) -> None:
        rprint(
            f"[cyan]Kobler til Bambu-skriver {self.serial} på {self.ip} (kamera={'on' if self.connectCamera else 'off'})...[/cyan]"
        )
        self.printer = bl.Printer(self.ip, self.accessCode, self.serial)
        if self.connectCamera:
            self.printer.connect()
        else:
            self.printer.mqtt_start()
        self.waitForMqttReady(timeout=30.0)
        state = self.printer.get_state()
        rprint(f"[green]MQTT klar. Skriverstatus: {state}[/green]")

    def disconnectPrinter(self) -> None:
        if not self.printer:
            return
        try:
            self.printer.disconnect()
        except Exception:
            try:
                self.printer.mqtt_stop()
            except Exception:
                pass

    @staticmethod
    def _zipGcodeToThreeMfBytes(gcodeText: str, platePath: str = "Metadata/plate_1.gcode") -> io.BytesIO:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(platePath, gcodeText)
        buffer.seek(0)
        return buffer

    def _publishProjectFileSpool(self, uploadName: str, param: str | int) -> None:
        raise NotImplementedError("Spooling via raw MQTT is disabled by API-only policy")

    def _safeCall(self, function, *args, **kwargs):
        self.waitForMqttReady(timeout=15.0)
        return function(*args, **kwargs)

    def _publishControl(self, payload: dict) -> None:
        if not self.printer:
            raise RuntimeError("Ikke tilkoblet")
        self.waitForMqttReady(timeout=10.0)
        try:
            if hasattr(self.printer, "publish"):
                return self.printer.publish(payload)
            if hasattr(self.printer, "send_request"):
                return self.printer.send_request(payload)
        except Exception as error:
            rprint(f"[yellow]Wrapper publish feilet: {error}[/yellow]")

        raise RuntimeError("Ingen tilgjengelig API-transport for kontrollpayload (API-only policy)")

    def pausePrint(self) -> None:
        if hasattr(self.printer, "pause_print"):
            return self._safeCall(self.printer.pause_print)
        return self._publishControl({"print": {"command": "pause"}})

    def resumePrint(self) -> None:
        if hasattr(self.printer, "resume_print"):
            return self._safeCall(self.printer.resume_print)
        return self._publishControl({"print": {"command": "resume"}})

    def stopPrint(self) -> None:
        if hasattr(self.printer, "stop_print"):
            return self._safeCall(self.printer.stop_print)
        return self._publishControl({"print": {"command": "stop"}})

    def skipCurrentObject(self) -> None:
        if hasattr(self.printer, "skip_object"):
            return self._safeCall(self.printer.skip_object)
        return self._publishControl({"print": {"command": "skip_object"}})

    def homeAll(self) -> None:
        if hasattr(self.printer, "home_all"):
            return self._safeCall(self.printer.home_all)
        return self._publishControl({"motion": {"command": "home_all"}})

    def moveAxis(self, axis: str, distance: float, feedrate: int = 3000) -> None:
        axisValue = axis.lower()
        if hasattr(self.printer, "move"):
            return self._safeCall(self.printer.move, axisValue, distance, feedrate)
        payload = {
            "motion": {
                "command": "move",
                "axis": axisValue,
                "distance": float(distance),
                "feedrate": int(feedrate),
            }
        }
        return self._publishControl(payload)

    def setBedTemp(self, temperature: int) -> None:
        if hasattr(self.printer, "set_bed_temperature"):
            return self._safeCall(self.printer.set_bed_temperature, temperature)
        return self._publishControl({"temperature": {"bed": {"target": int(temperature)}}})

    def setNozzleTemp(self, temperature: int) -> None:
        if hasattr(self.printer, "set_nozzle_temperature"):
            return self._safeCall(self.printer.set_nozzle_temperature, temperature)
        return self._publishControl({"temperature": {"nozzle": {"target": int(temperature)}}})

    def coolDown(self) -> None:
        if hasattr(self.printer, "cool_down"):
            return self._safeCall(self.printer.cool_down)
        payload = {"temperature": {"bed": {"target": 0}, "nozzle": {"target": 0}}}
        return self._publishControl(payload)

    def uploadAndStart(
        self,
        inputPath: Path,
        uploadName: str,
        plate: Optional[int] = 1,
        gcodeInsidePath: Optional[str] = None,
        spool: bool = False,
        useAms: Optional[bool] = None,
        jobMetadata: Optional[dict] = None,
    ) -> None:
        if not self.printer:
            raise RuntimeError("Ikke tilkoblet. Kall connectPrinter() først.")

        suffix = inputPath.suffix.lower()
        if suffix == ".3mf":
            data: io.BufferedReader | io.BytesIO = open(inputPath, "rb")
            plateIndex = plate or 1
            spoolParam = f"Metadata/plate_{plateIndex}.gcode"
            startParam = plateIndex if plate is not None else spoolParam
        elif suffix == ".gcode":
            gcodeText = inputPath.read_text(encoding="utf-8")
            platePath = gcodeInsidePath or "Metadata/plate_1.gcode"
            data = self._zipGcodeToThreeMfBytes(gcodeText, platePath)
            spoolParam = platePath
            startParam = plate if plate is not None else platePath
        else:
            raise ValueError("Støtter kun .3mf eller .gcode")

        if useAms is None:
            if jobMetadata and jobMetadata.get("ams_configuration") is None:
                useAms = False
            else:
                useAms = True
        if spool:
            useAms = False

        rprint(f"[cyan]Laster opp '{uploadName}' til skriver...[/cyan]")
        result = self.printer.upload_file(data, uploadName)
        if hasattr(data, "close"):
            try:
                data.close()  # type: ignore[call-arg]
            except Exception:
                pass
        if "226" not in str(result) and result is not True:
            raise RuntimeError(f"Opplasting feilet (FTP-respons: {result})")
        rprint("[green]Opplasting OK.[/green]")

        self.waitForMqttReady(timeout=20.0)
        rprint("[cyan]Sender start-kommando...[/cyan]")
        startArgument = spoolParam if spool else startParam
        if spool:
            self._publishProjectFileSpool(uploadName, startArgument)
        else:
            self.printer.start_print(uploadName, startArgument, use_ams=useAms)
        rprint(f"[green]Start-kommando sendt (use_ams={useAms}).[/green]")

        startTime = time.time()
        acknowledged = False
        while time.time() - startTime < 60:
            try:
                state = (self.printer.get_state() or "").lower()
                percentage = (self.printer.get_percentage() or 0) or 0
                if any(keyword in state for keyword in ("heat", "warm", "run", "print")) or percentage > 0:
                    acknowledged = True
                    break
            except Exception:
                pass
            time.sleep(1.5)

        if acknowledged:
            rprint("[green]Start-kommando bekreftet av skriver (heating/running).[/green]")
        else:
            rprint("[yellow]Fikk ikke eksplisitt start-ACK innen 60s – følger videre i monitor.[/yellow]")

    def monitorPrinter(self, interval: float = 5.0) -> None:
        if not self.printer:
            raise RuntimeError("Ikke tilkoblet. Kall connectPrinter() først.")
        console = Console()
        try:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                TimeElapsedColumn(),
                console=console,
            ) as progress:
                task = progress.add_task("Overvåker utskrift... [Ctrl+C for å stoppe]", start=False)
                progress.start_task(task)
                while True:
                    time.sleep(interval)
                    status = self.printer.get_state()
                    percentage = self.printer.get_percentage()
                    layer = self.printer.current_layer_num()
                    layers = self.printer.total_layer_num()
                    bed = self.printer.get_bed_temperature()
                    nozzle = self.printer.get_nozzle_temperature()
                    remain = self.printer.get_time()
                    fields = {
                        "status": status,
                        "progress_%": percentage,
                        "layer": layer,
                        "layers": layers,
                        "bed_C": bed,
                        "nozzle_C": nozzle,
                        "remaining_min": remain,
                    }
                    progress.update(task, description=f"{json.dumps(fields)}")
        except KeyboardInterrupt:
            rprint("\n[yellow]Monitor stoppet av bruker[/yellow]\n")

    def statusSnapshot(self) -> Dict[str, Any]:
        if not self.printer:
            return {"online": False}
        try:
            self.waitForMqttReady(timeout=1.0)
        except Exception:
            return {"online": False}
        return {
            "online": True,
            "status": self.printer.get_state(),
            "progress": self.printer.get_percentage(),
            "layer": self.printer.current_layer_num(),
            "layers": self.printer.total_layer_num(),
            "bed": self.printer.get_bed_temperature(),
            "nozzle": self.printer.get_nozzle_temperature(),
            "time_remaining_min": self.printer.get_time(),
            "ip": self.ip,
            "serial": self.serial,
            "firmware": getattr(self.printer, "firmware_version", None),
        }


def subscribeReportRaw(ip: str, serial: str, accessCode: str) -> None:
    raise NotImplementedError("Raw MQTT subscription is disabled by API-only policy")


def openInBambuConnect(threeMfPath: Path, displayName: Optional[str] = None) -> None:
    if displayName is None:
        displayName = threeMfPath.stem
    from urllib.parse import quote

    url = "bambu-connect://import-file?" f"path={quote(str(threeMfPath))}&name={quote(displayName)}&version=1.0.0"
    rprint(f"[cyan]Åpner: {url}[/cyan]")
    systemName = platform.system()
    try:
        if systemName == "Darwin":
            subprocess.run(["open", url], check=True)
        elif systemName == "Windows":
            subprocess.run(["start", url], shell=True, check=True)
        else:
            subprocess.run(["xdg-open", url], check=True)
    except Exception as error:
        rprint(f"[red]Klarte ikke å åpne Bambu Connect URL: {error}[/red]")


def parseUseAms(value: str) -> Optional[bool]:
    mapping = {"auto": None, "true": True, "false": False}
    return mapping[value]


def resolvePrinterCredentials(args, jobMetadata: Dict[str, Any]) -> tuple[str, str, str]:
    if jobMetadata:
        try:
            return updatePrinterFromJob(jobMetadata)
        except ValueError:
            if args.serial and args.ip and args.accessCode:
                rprint("[yellow]Job metadata manglet fullstendig LAN-info, bruker argumentverdier.[/yellow]")
                return args.serial, args.ip, args.accessCode
            raise
    if not (args.serial and args.ip and args.accessCode):
        raise ValueError("Manglende printerinformasjon. Oppgi --serial, --ip og --access-code")
    return args.serial, args.ip, args.accessCode


def runCli() -> None:
    parser = argparse.ArgumentParser(description="Send og kontroller utskrifter på Bambu via LAN.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sendParser = subparsers.add_parser("send", help="Last opp og start en utskrift")
    sendParser.add_argument("file", type=Path, help="Bane til .3mf eller .gcode")
    sendParser.add_argument("--ip", help="Skriverens IP-adresse i LAN")
    sendParser.add_argument("--serial", help="Skriverens serienummer (15 tegn)")
    sendParser.add_argument("--access-code", dest="accessCode", help="LAN Access Code fra skriverens skjerm")
    sendParser.add_argument("--plate", type=int, default=1, help="Plate-nummer for .3mf (standard 1)")
    sendParser.add_argument("--gcode-path", dest="gcodePath", help="Valgfri sti i .3mf for gcode")
    sendParser.add_argument("--upload-name", dest="uploadName", help="Navn på opplastet .3mf i skriveren")
    sendParser.add_argument("--connect-camera", dest="connectCamera", action="store_true", help="Koble også til kamera")
    sendParser.add_argument("--monitor", action="store_true", help="Kjør en enkel sanntidsmonitor etter start")
    sendParser.add_argument("--raw-report", dest="rawReport", action="store_true", help="Kjør rå MQTT-abonnement i stedet for monitor")
    sendParser.add_argument("--spool", action="store_true", help="Start fra ekstern spole")
    sendParser.add_argument("--use-ams", dest="useAms", choices=["auto", "true", "false"], default="auto")
    sendParser.add_argument("--job-metadata", dest="jobMetadata", help="Fil med jobbinformasjon i JSON-format")
    sendParser.add_argument("--use-bambu-connect", dest="useBambuConnect", action="store_true", help="Bruk Bambu Connect i stedet for LAN (krever .3mf)")

    controlParser = subparsers.add_parser("ctrl", help="Kontroller en aktiv utskrift")
    controlParser.add_argument("--ip", required=True, help="Skriverens IP-adresse i LAN")
    controlParser.add_argument("--serial", required=True, help="Skriverens serienummer (15 tegn)")
    controlParser.add_argument("--access-code", dest="accessCode", required=True, help="LAN Access Code")
    controlParser.add_argument(
        "action",
        choices=["pause", "resume", "stop", "skip", "home", "cooldown", "move"],
        help="Handling som skal utføres",
    )
    controlParser.add_argument("--axis", choices=["x", "y", "z"], help="Akse for move (ved behov)")
    controlParser.add_argument("--distance", type=float, help="Avstand i mm for move")
    controlParser.add_argument("--feedrate", type=int, default=3000, help="Feedrate for move (mm/min)")

    args = parser.parse_args()

    if args.command == "send":
        filePath: Path = args.file
        if not filePath.exists():
            rprint(f"[red]Filen finnes ikke: {filePath}[/red]")
            sys.exit(1)

        if args.useBambuConnect:
            if filePath.suffix.lower() != ".3mf":
                rprint("[red]Bambu Connect-import krever .3mf. Lagre/eksporter som .3mf først.[/red]")
                sys.exit(2)
            openInBambuConnect(filePath)
            sys.exit(0)

        jobMetadata: Dict[str, Any] = {}
        if args.jobMetadata:
            jobMetadata = json.loads(Path(args.jobMetadata).read_text(encoding="utf-8"))

        serial: str
        ip: str
        accessCode: str
        try:
            serial, ip, accessCode = resolvePrinterCredentials(args, jobMetadata)
        except Exception as error:
            rprint(f"[bold red]{error}[/bold red]")
            sys.exit(2)

        uploadName = sanitizeUploadName(args.uploadName or (filePath.stem + ".3mf"))
        useAmsValue = parseUseAms(args.useAms)

        client = BambuLanClient(ip, serial, accessCode, connectCamera=args.connectCamera)

        try:
            client.connectPrinter()
            client.uploadAndStart(
                filePath,
                uploadName,
                plate=args.plate,
                gcodeInsidePath=args.gcodePath,
                spool=args.spool,
                useAms=useAmsValue,
                jobMetadata=jobMetadata,
            )
            if args.monitor:
                if args.rawReport:
                    subscribeReportRaw(ip, serial, accessCode)
                else:
                    client.monitorPrinter(interval=5.0)
        except KeyboardInterrupt:
            rprint("\n[yellow]Avbrutt av bruker.[/yellow]")
        except Exception as error:
            rprint(f"[bold red]Feil: {error}[/bold red]")
            sys.exit(2)
        finally:
            try:
                client.disconnectPrinter()
            except Exception:
                pass

    elif args.command == "ctrl":
        client = BambuLanClient(args.ip, args.serial, args.accessCode)
        try:
            client.connectPrinter()
            action = args.action
            if action == "pause":
                client.pausePrint()
            elif action == "resume":
                client.resumePrint()
            elif action == "stop":
                client.stopPrint()
            elif action == "skip":
                client.skipCurrentObject()
            elif action == "home":
                client.homeAll()
            elif action == "cooldown":
                client.coolDown()
            elif action == "move":
                if not args.axis or args.distance is None:
                    raise ValueError("Move krever --axis og --distance")
                client.moveAxis(args.axis, args.distance, args.feedrate)
        except KeyboardInterrupt:
            rprint("\n[yellow]Avbrutt av bruker.[/yellow]")
        except Exception as error:
            rprint(f"[bold red]Feil: {error}[/bold red]")
            sys.exit(2)
        finally:
            try:
                client.disconnectPrinter()
            except Exception:
                pass


if __name__ == "__main__":
    runCli()
