# Cloud Printer API

## Oversikt

Dette prosjektet inneholder Cloud Run-APIet som håndterer printer-kommandoer, statusoppdateringer og jobbinformasjon via Firestore.

Den tidligere LAN-klienten (`client`-mappen) er nå flyttet til et eget prosjekt.

## Arkitektur

APIet fungerer som baksystem for både frontend og den frittstående printer-klienten.

```
+------------+        HTTPS         +----------------+        Firestore        +----------------+
|  Klienter  |  ─────────────────▶  | Cloud Run API  |  ───────────────────▶  | printer_* docs |
+------------+                      +----------------+                         +----------------+
```

## API-endepunkter

**Base-URL-er**

- `https://printpro3d-api-931368217793.europe-west1.run.app` – Produksjon
- Lokal utvikling: `http://localhost:8080` (krever kjørende instans)

Se kildekoden eller tidligere dokumentasjon for detaljer om endepunktene `/control`, `/products/<productId>/handshake`, `/api/apps/<appId>/functions/updatePrinterStatus`, og `/debug/listPendingCommands`.

## Miljøvariabler

Påkrevde miljøvariabler for kjøring:

```
GCP_PROJECT_ID=<prosjekt-id>
GCS_BUCKET_NAME=<bucket-navn>
KMS_KEY_RING=<key-ring>
KMS_KEY_NAME=<key-name>
KMS_LOCATION=<location>
API_KEYS_PRINTER_STATUS=<api-nøkler>
```

For lokal kjøring, sørg for at `GOOGLE_APPLICATION_CREDENTIALS` peker til en gyldig service account-nøkkelfil.
