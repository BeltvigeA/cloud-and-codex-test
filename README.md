Cloud Printer Backend & LAN Client
=================================

Oversikt
--------
Denne koden består av to hoveddeler:

* Et Cloud Run-API som tar imot kommandoer, printerstatus og jobbinformasjon og lagrer dem i Firestore.
* En LAN-klient som kjører hos sluttbruker, kommuniserer direkte med Bambu-skriveren, og bruker Firestore til å hente kommandoer og rapportere status.

Arkitektur
----------
Sekvensen nedenfor viser de viktigste interaksjonene mellom komponentene:

```
+------------+        HTTPS         +----------------+        Firestore        +----------------+
|  Klienter  |  ─────────────────▶  | Cloud Run API  |  ───────────────────▶  | printer_* docs |
+------------+                      +----------------+                         +----------------+
        ▲                                   │                                           │
        │                                   │                                           ▼
        │                                   │                           LAN-klient poller/oppdaterer
        │                                   ▼
        │                           Kommandoer legges i Firestore
        │                                                                                 
        │   MQTT/HTTP (LAN)                                                             
        └──────────────────────────────────────────────────────────────────────────────▶ Skriver
```

Typisk flyt for en kommando:

1. En ekstern klient kaller `POST /control` på API-et.
2. API-et skriver et dokument i `printer_commands` med status `pending`.
3. LAN-klienten poller Firestore, henter kommandoen og logger hendelsene.
4. Kommandoen gjennomføres mot skriveren over LAN.
5. Klienten oppdaterer dokumentet i Firestore til `completed` eller `failed`.

API-endepunkter
----------------
Alle URL-er er prefikset med produksjonsbasen `https://printer-backend-934564650450.europe-west1.run.app`.

### 1. `POST /control`
* **Full URL:** `https://printer-backend-934564650450.europe-west1.run.app/control`
* **Formål:** Opprette en ny printer-kommando i `printer_commands`.
* **Headers:** `Content-Type: application/json`, `X-API-Key: <key>`.
* **Eksempelrequest:**
  ```json
  {
    "recipientId": "RID123",
    "printerSerial": "01P00A381200434",
    "commandType": "heat",
    "metadata": { "bedTemp": 80 },
    "expiresAt": "2025-10-15T05:53:28Z"
  }
  ```
* **Resultat:** Dokument i Firestore med status `pending` og automatisk `createdAt`.

### 2. `POST /products/<productId>/handshake`
* **Full URL:** `https://printer-backend-934564650450.europe-west1.run.app/products/<productId>/handshake`
* **Formål:** Tilby metadata og filreferanser før opplasting/print.
* **Klientbruk:** LAN-klienten eller Base44 bruker endepunktet til å gjøre klar nødvendige ressurser.

### 3. `POST /api/apps/<appId>/functions/updatePrinterStatus`
* **Full URL:** `https://printer-backend-934564650450.europe-west1.run.app/api/apps/<appId>/functions/updatePrinterStatus`
* **Formål:** Ta imot statusoppdateringer (temperatur, fremdrift osv.) fra LAN-klienten.
* **Resultat:** Data lagres i `printer_telemetry` og brukes for visning og overvåking.

### 4. `POST /debug/listPendingCommands`
* **Full URL:** `https://printer-backend-934564650450.europe-west1.run.app/debug/listPendingCommands`
* **Formål:** Manuell inspeksjon av ventende kommandoer når man feilsøker.
* **Eksempelrequest:**
  ```json
  { "recipientId": "RID123" }
  ```

Firestore-modell og indekser
----------------------------

### Samlinger
* `printer_commands`: Kommandoer som skal eksekveres. Viktige felter: `recipientId`, `status`, `createdAt`, `commandType`, `metadata`, `expiresAt`, `printerSerial`, `printerIpAddress`.
* `printer_telemetry`: Status- og helsedata sendt fra LAN-klienten.
* `print_jobs`: Valgfritt – historikk over opplastinger og jobber.

### Komposittindeks
For at Firestore skal støtte kombinasjonen `recipientId == ?` + `status == 'pending'` + `order_by(createdAt desc)` må følgende indeks være distribuert. Filen `firestore.indexes.json` i repoet inneholder definisjonen:

```json
{
  "indexes": [
    {
      "collectionGroup": "printer_commands",
      "queryScope": "COLLECTION",
      "fields": [
        { "fieldPath": "recipientId", "order": "ASCENDING" },
        { "fieldPath": "status", "order": "ASCENDING" },
        { "fieldPath": "createdAt", "order": "DESCENDING" }
      ]
    }
  ],
  "fieldOverrides": []
}
```

Deploy indeksen med `firebase deploy --only firestore:indexes`, eller last opp JSON-konfigurasjonen direkte via Firestore Console.

LAN-klientens oppførsel
-----------------------

* **Polling:** `_listPendingCommandsFromFirestore` henter kommandoer per mottaker, logger `poll_start`, `poll_ok` og `incoming_item`.
* **Robusthet:** Hvis Firestore svarer med feilen «The query requires an index», logges feilen og klienten kjører en fallback-spørring uten `order_by`. Resultatet sorteres lokalt slik at GUI-et fortsatt viser kommandoer mens indeksen er under oppbygging.
* **Fullføring:** `_completeCommandInFirestore` oppdaterer `status`, `completedAt` og eventuelle feilmeldinger.

Integrasjon mot Base44
----------------------

Base44 er den eksterne tjenesten som forsyner systemet med printjobber og metadata for produktene. Når du bygger integrasjonen må du følge denne prosessen steg for steg:

1. **Autentisering mot Base44:**
   * Hent API-nøkkel fra Base44-administrasjonen og lagre den sikkert som `BASE44_API_KEY` i secrets store (for eksempel Secret Manager).
   * Alle HTTP-kall sendes til `https://api.base44.com` med `Authorization: Bearer <BASE44_API_KEY>` og `Content-Type: application/json`.
2. **Opprette en jobbrekvisisjon:**
   * Send `POST /v1/print-jobs` til Base44 med følgende minimumsdata:
     ```json
     {
       "recipientId": "RID123",
       "printerSerial": "01P00A381200434",
       "productId": "PROD-55",
       "jobMetadata": {
         "gcodeUrl": "https://storage.googleapis.com/.../file.gcode",
         "estimatedPrintTimeSeconds": 5400
       }
     }
     ```
   * Base44 svarer med et JSON-objekt som inneholder `jobId`, `productId`, `status` og en `files`-liste. Lagre `jobId` i Firestore under `print_jobs` slik at både Cloud Run og LAN-klienten kan hente den senere.
3. **Synkronisere filer til LAN-klienten:**
   * For hver oppføring i `files`-listen fra Base44 må du opprette et dokument i `printer_commands` med `commandType` satt til `download` og en `metadata.fileUrl` som peker direkte til filen.
   * Når LAN-klienten ser `download`-kommandoen, laster den ned filen og lagrer en lokal checksum. Oppdater dokumentet med `status="completed"` og legg til `metadata.downloadChecksum` for sporbarhet.
4. **Starte print hos Base44 og i LAN-klienten:**
   * Etter at alle filer er synkronisert send `POST /v1/print-jobs/{jobId}/start` til Base44 med den nøyaktige IP-adressen til skriveren (`printerIpAddress`) og den valgte preset-en (`printProfileId`).
   * Samtidig oppretter Cloud Run et nytt dokument i `printer_commands` med `commandType="start"`, `metadata.jobId=<jobId>` og samme `printerIpAddress`. Dette sikrer at LAN-klienten starter printen lokalt i takt med Base44.
5. **Rapportere status tilbake til Base44:**
   * LAN-klienten samler status gjennom `_collectPrinterTelemetry` og publiserer det til Cloud Run via `updatePrinterStatus`.
   * Cloud Run sender videre `POST /v1/print-jobs/{jobId}/status` til Base44 med felter som `progressPercent`, `bedTemp`, `nozzleTemp` og `lastEventAt`. På den måten holder Base44 dashboardet seg oppdatert.

> **Merk:** Alle tidsstempler sendes i ISO 8601 (`YYYY-MM-DDTHH:MM:SSZ`) og alle numeriske verdier (temperaturer, prosenter, etc.) rapporteres i metriske enheter som forventet av Base44.

Miljøvariabler (hurtigstart)
---------------------------

```
FIRESTORE_PROJECT_ID=print-pipe-demo
# eller
GCP_PROJECT_ID=print-pipe-demo
# og for lokal autentisering:
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
```

Eksempel på akseptansekriterier
--------------------------------

1. Opprett et dokument i `printer_commands` med `status="pending"` og korrekt `recipientId`.
2. LAN-klienten viser i loggene:
   * `poll_start (source=firestore)`
   * `poll_ok (count >= 1)`
   * `incoming_item (commandId=..., commandType=...)`
3. Uten gyldig Firestore-tilgang skal klienten logge `firestore_client_error` eller `firestore_poll_failed` i stedet for å være stille.
