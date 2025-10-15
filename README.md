Cloud Printer Backend & Client (LAN + Firestore)
===============================================

Hva er dette?
-------------

Cloud Run API for å motta jobber, kontrollere skrivere og lagre tilstand.

Klient som kjører lokalt, snakker med Bambu over LAN (MQTT/FTPS) og med Firestore.

Miljø (hurtigstart)
-------------------

API base URL (prod):
https://printer-backend-934564650450.europe-west1.run.app

Firestore prosjekt:
print-pipe-demo (sett FIRESTORE_PROJECT_ID eller GCP_PROJECT_ID)

Klient (miljøvariabler):

```
FIRESTORE_PROJECT_ID=print-pipe-demo
# eller
GCP_PROJECT_ID=print-pipe-demo
# og ev. lokal autentisering:
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
```

API-endepunkter (med fulle URL-er)
----------------------------------

1) POST /control

URL: https://printer-backend-934564650450.europe-west1.run.app/control
Headers: X-API-Key: <key>
Body:

```
{
  "recipientId": "…",
  "printerSerial": "01P00A381200434",
  "commandType": "heat",
  "metadata": { "bedTemp": 80 },
  "expiresAt": "2025-10-15T05:53:28Z"
}
```

Effekt: Oppretter dokument i Firestore printer_commands med status="pending".

2) POST /products/<productId>/handshake

URL: https://printer-backend-934564650450.europe-west1.run.app/products/<productId>/handshake
Bruk: Klient for å hente filreferanser/metadata før opplasting/print.

3) POST /api/apps/<appId>/functions/updatePrinterStatus

URL: https://printer-backend-934564650450.europe-west1.run.app/api/apps/<appId>/functions/updatePrinterStatus
Bruk: Klient poster snapshots (temperatur, progress, state). Lagres i printer_telemetry.

4) (Valgfritt debug) POST /debug/listPendingCommands

URL: https://printer-backend-934564650450.europe-west1.run.app/debug/listPendingCommands
Body:

```
{ "recipientId": "…" }
```

Bruk: Direkte test av backend-tilgjengelige kommandoer (om Base44 mangler).

Firestore Collections
---------------------

printer_commands

Felter: recipientId, commandId, commandType, metadata, status, createdAt, (ev. expiresAt, printerSerial, printerIpAddress)

Klientens poller: Leser WHERE recipientId==RID AND status=="pending".

printer_telemetry

Status og målinger postet av klienten.

print_jobs

Opplasting/print-jobb journal (valgfritt, hvis aktivert).

Klienten – hvordan den virker
-----------------------------

Oppsett
~~~~~~~

GUI for Recipient ID, IP/Serial for printere, og API-nøkkel.

Start Listening → starter:

- StatusReporter (Base44) – pusher snapshots til API.
- CommandPoller (Firestore) – henter pending-kommandoer.

CommandPoller (viktig)
~~~~~~~~~~~~~~~~~~~~~~

Henter fra Firestore (printer_commands) hvert ~5 s.

Logger i Logs:

- control: poll_start (source=firestore)
- control: poll_ok (count=…)
- control: incoming_item (commandId, commandType)

Feil: control: firestore_client_error / control: firestore_poll_failed / control: poll_exception

Kan trigge kjeding til executor (valgfritt) – i denne versjonen logger vi først for observabilitet.

Logging (kategorier)
~~~~~~~~~~~~~~~~~~~~

- listener – fil-/handshake/GUI-operasjoner
- control – kommando-henting/utførelse
- status-printer – telemetri/health/MQTT
- status-base44 – posting til API
- print-job – opplasting/start/progress/ferdig/feil
- conn-error – tilkoblingsfeil (default av i GUI-filter)

Eksempelflyt (kommando)
~~~~~~~~~~~~~~~~~~~~~~~

Tjenesten (eller Base44) kaller
POST https://printer-backend-934564650450.europe-west1.run.app/control
med commandType=heat, metadata={"bedTemp":80}, recipientId="RID".

API lager dokument i Firestore printer_commands:

```
{
  commandId: "...",
  recipientId: "RID",
  commandType: "heat",
  metadata: { bedTemp: 80 },
  status: "pending",
  createdAt: <timestamp>
}
```

Klienten (CommandPoller) henter → Logs → control viser:

- poll_start (source=firestore)
- poll_ok (count=1)
- incoming_item (commandId="...", commandType="heat")

Feilsøking
~~~~~~~~~~

Ingen logs i control:
Bekreft:

- FIRESTORE_PROJECT_ID/GCP_PROJECT_ID er satt i klientens miljø.
- Tjenestekonto/credentials (lokalt) via GOOGLE_APPLICATION_CREDENTIALS.
- Dokumenter i printer_commands har riktig recipientId & status="pending".
- Se etter control: firestore_client_error/firestore_poll_failed.

For mange MQTT-feil i “listener”:
Kategori-resolver i logbus ruter nå mqtt_client/paho-logger til conn-error.

5) Akseptansekriterier (må oppfylles)
-------------------------------------

Legg inn dokument i printer_commands (status pending, korrekt recipientId).
Innen ~5 sek skal GUI vise i Logs → control:

poll_start (source=firestore) → poll_ok (count≥1) → incoming_item.

Uten gyldig Firestore-tilgang skal GUI vise feil i Logs → control (ikke være helt stille).

6) Endringsliste (kode)
-----------------------

- client/command_poller.py – NY fil ifølge 3.1.
- client/commands.py – styr Firestore først, eksplisitt logging (3.2).
- client/gui.py – importer/start/stopp poller + oppdatere recipient (3.3).
- (README) – erstatt med innholdet i kapittel 4.
