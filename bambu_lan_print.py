#!/usr/bin/env python3
"""
Kompleks eksempel: Send .3mf eller .gcode til Bambu-skriver over LAN og start print via MQTT,
med valgfri sanntidsmonitor og fallback til Bambu Connect.

Avhenger av:
  pip install bambulabs_api paho-mqtt rich

Støtter:
- LAN (Developer Mode anbefales): FTPS-opplasting + MQTT start (via bambulabs_api)
- .gcode pakkes automatisk inn i .3mf-container (ZIP) som Metadata/plate_1.gcode
- Sanntidsstatus (progress, lag, tid, temperaturer)
- Valgfritt: rå MQTT-abonnement for status (lesing) uten bibliotek
- Fallback: Åpne Bambu Connect med URL-skjema hvis du ikke bruker Developer Mode

Merk:
- Bruker TLS mot MQTT (port 8883) og FTPS (port 990) via biblioteket.
- MQTT brukernavn er 'bblp' og passord er LAN Access Code (håndteres av biblioteket når du oppgir access code).
- Krever skriver-IP, serienummer og LAN Access Code fra skriverens skjerm.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
import zipfile
import ssl
import platform
import subprocess
from pathlib import Path
from typing import Optional

from rich import print as rprint
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

try:
    import bambulabs_api as bl
except ImportError as e:
    rprint("[bold red]Mangler 'bambulabs_api'. Kjør: pip install bambulabs_api[/bold red]")
    raise

# paho-mqtt er valgfritt (for rå status-abonnement)
try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None  # Håndteres senere


class BambuLANClient:
    """Høynivå-klient som bruker bambulabs_api for MQTT+FTPS."""

    def __init__(self, ip: str, serial: str, access_code: str, connect_camera: bool = False):
        self.ip = ip
        self.serial = serial
        self.access_code = access_code
        self.connect_camera = connect_camera
        self.printer: Optional[bl.Printer] = None

    def connect(self) -> None:
        rprint(f"[cyan]Kobler til Bambu-skriver {self.serial} på {self.ip} (kamera={'on' if self.connect_camera else 'off'})...[/cyan]")
        self.printer = bl.Printer(self.ip, self.access_code, self.serial)
        if self.connect_camera:
            self.printer.connect()
        else:
            self.printer.mqtt_start()  # kun mqtt, ingen kameratilkobling
        time.sleep(1.5)
        state = self.printer.get_state()
        rprint(f"[green]Tilkoblet. Skriverstatus: {state}[/green]")

    def disconnect(self) -> None:
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
    def _zip_gcode_to_3mf_bytes(gcode_text: str, plate_path: str = "Metadata/plate_1.gcode") -> io.BytesIO:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(plate_path, gcode_text)
        buf.seek(0)
        return buf

    def upload_and_start(self, input_path: Path, upload_name: str, plate: Optional[int] = 1, gcode_inside_path: Optional[str] = None) -> None:
        if not self.printer:
            raise RuntimeError("Ikke tilkoblet. Kall connect() først.")
        suffix = input_path.suffix.lower()
        if suffix == ".3mf":
            data: io.BufferedReader | io.BytesIO = open(input_path, "rb")
            start_arg = plate if plate is not None else 1
        elif suffix == ".gcode":
            gcode_text = input_path.read_text(encoding="utf-8")
            plate_path = gcode_inside_path or "Metadata/plate_1.gcode"
            data = self._zip_gcode_to_3mf_bytes(gcode_text, plate_path)
            # Når start_print brukes med path-argument for gcode i .3mf, må vi sende stien i containeren
            start_arg = plate if plate is not None else plate_path
        else:
            raise ValueError("Støtter kun .3mf eller .gcode")

        rprint(f"[cyan]Laster opp '{upload_name}' til skriver...[/cyan]")
        result = self.printer.upload_file(data, upload_name)
        # FTPS 226 = Transfer complete
        if "226" not in str(result):
            raise RuntimeError(f"Opplasting feilet (FTP-respons: {result})")
        rprint("[green]Opplasting OK.[/green]")

        rprint("[cyan]Sender start-kommando...[/cyan]")
        self.printer.start_print(upload_name, start_arg)
        rprint("[green]Start-kommando sendt.[/green]")

    def monitor(self, interval: float = 5.0) -> None:
        if not self.printer:
            raise RuntimeError("Ikke tilkoblet. Kall connect() først.")
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


def subscribe_report_raw(ip: str, serial: str, access_code: str) -> None:
    """
    Rå MQTT-abonnement på device/<serial>/report for å vise JSON-status i sanntid.
    Krever paho-mqtt installert.
    """
    if mqtt is None:
        rprint("[red]paho-mqtt er ikke installert. Kjør: pip install paho-mqtt[/red]")
        sys.exit(2)

    topic = f"device/{serial}/report"
    client = mqtt.Client()
    # Bambu bruker selvsignert cert; vi deaktiverer verifisering for enkelhets skyld.
    client.tls_set(cert_reqs=ssl.CERT_NONE)
    client.tls_insecure_set(True)
    client.username_pw_set("bblp", access_code)

    def on_connect(cl, userdata, flags, rc):
        if rc == 0:
            rprint(f"[green]Rå MQTT tilkoblet. Abonnerer på {topic}[/green]")
            cl.subscribe(topic)
        else:
            rprint(f"[red]MQTT-tilkobling feilet rc={rc}[/red]")

    def on_message(cl, userdata, msg):
        try:
            payload = msg.payload.decode("utf-8", errors="ignore")
            # Meldinger er ofte JSON (noen ganger pakket), vi prøver JSON først
            try:
                data = json.loads(payload)
                rprint(data)
            except json.JSONDecodeError:
                rprint(payload)
        except Exception as e:
            rprint(f"[red]Feil ved parsing av melding: {e}[/red]")

    client.on_connect = on_connect
    client.on_message = on_message
    rprint(f"[cyan]Kobler til MQTT {ip}:8883 som bblp[/cyan]")
    client.connect(ip, 8883, keepalive=60)
    try:
        client.loop_forever()
    except KeyboardInterrupt:
        rprint("\n[yellow]Avslutter rå MQTT-abonnement[/yellow]")


def open_in_bambu_connect(three_mf_path: Path, display_name: Optional[str] = None) -> None:
    """Åpne Bambu Connect via URL-skjema med en 3MF-fil."""
    if display_name is None:
        display_name = three_mf_path.stem
    # Bygg URL i henhold til skjemaet bambu-connect://import-file?path=...&name=...&version=1.0.0
    from urllib.parse import quote
    url = (
        "bambu-connect://import-file?"
        f"path={quote(str(three_mf_path))}&name={quote(display_name)}&version=1.0.0"
    )
    rprint(f"[cyan]Åpner: {url}[/cyan]")
    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.run(["open", url], check=True)
        elif system == "Windows":
            subprocess.run(["start", url], shell=True, check=True)
        else:
            # Linux: xdg-open (Connect for Linux er underveis; URL kan forberedes)
            subprocess.run(["xdg-open", url], check=True)
    except Exception as e:
        rprint(f"[red]Klarte ikke å åpne Bambu Connect URL: {e}[/red]")


def main() -> None:
    p = argparse.ArgumentParser(description="Send og start utskrifter på Bambu via LAN eller Bambu Connect.")
    p.add_argument("file", type=Path, help="Bane til .3mf eller .gcode")
    p.add_argument("--ip", required=True, help="Skriverens IP-adresse i LAN")
    p.add_argument("--serial", required=True, help="Skriverens serienummer (15 tegn)")
    p.add_argument("--access-code", required=True, help="LAN Access Code fra skriverens skjerm")
    p.add_argument("--plate", type=int, default=1, help="Plate-nummer for .3mf (standard 1). Ignorert for rå gcode når sti oppgis.")
    p.add_argument("--gcode-path", default=None, help="Valgfri sti i .3mf for gcode (f.eks. Metadata/plate_1.gcode)")
    p.add_argument("--upload-name", default=None, help="Navn på opplastet .3mf i skriveren. Standard: filnavn med .3mf-suffiks")
    p.add_argument("--connect-camera", action="store_true", help="Koble også til kamera (port 6000)")
    p.add_argument("--monitor", action="store_true", help="Kjør en enkel sanntidsmonitor etter start")
    p.add_argument("--raw-report", action="store_true", help="Kjør rå MQTT-abonnement i stedet for bibliotekets monitor")
    p.add_argument("--use-bambu-connect", action="store_true", help="Bruk Bambu Connect (åpner app via URL-skjema). Krever .3mf.")

    args = p.parse_args()

    file_path: Path = args.file
    if not file_path.exists():
        rprint(f"[red]Filen finnes ikke: {file_path}[/red]")
        sys.exit(1)

    # Hvis brukeren eksplisitt vil bruke Bambu Connect, åpner vi URL og avslutter.
    if args.use_bambu_connect:
        if file_path.suffix.lower() != ".3mf":
            rprint("[red]Bambu Connect-import krever .3mf. Lagre/eksporter som .3mf først.[/red]")
            sys.exit(2)
        open_in_bambu_connect(file_path)
        sys.exit(0)

    # LAN med biblioteket
    client = BambuLANClient(args.ip, args.serial, args.access_code, connect_camera=args.connect_camera)
    upload_name = args.upload_name or (file_path.stem + ".3mf")

    try:
        client.connect()
        client.upload_and_start(file_path, upload_name, plate=args.plate, gcode_inside_path=args.gcode_path)
        if args.monitor:
            if args.raw_report:
                subscribe_report_raw(args.ip, args.serial, args.access_code)
            else:
                client.monitor(interval=5.0)
    except KeyboardInterrupt:
        rprint("\n[yellow]Avbrutt av bruker.[/yellow]")
    except Exception as e:
        rprint(f"[bold red]Feil: {e}[/bold red]")
        sys.exit(2)
    finally:
        try:
            client.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    main()
