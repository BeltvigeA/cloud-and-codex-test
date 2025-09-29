codex & cloud 


Project ID=print-pipe-demo
GCS_BUCKET_NAME=3mf-gcode-container
KMS_KEY_RING=my-printer-keyring
KMS_KEY_NAME=printer-data-key
KMS_LOCATION=europe-west1
FIRESTORE_COLLECTION_FILES=print_jobs
FIRESTORE_COLLECTION_PRINTER_STATUS=printer_telemetry
SECRET_MANAGER_API_KEYS_PATH=projects/934564650450/secrets/printer-api-keys/versions/latest


SECRET_MANAGER_API_KEYS_PATH keys=
1ORJkv4IZtQjYIniGFX8fr340VreiBhK1XNcDZ3GVlaNSPSCkm6EIZy4m6XOJDF0XAPLcELuZSQnEHxvBMqhD9b5q5Klf0QE9fwih9TOgC2K643cOrhOPZJMVwb9BV7i5Q7R8u8mxPutdWz0RVXP7w

c3Lr1YyProjUnzf2GeG8MeGYb0UWNt5jnZLd6Svk7DvysymtwkcJatQC4xlsdK9Cy3h4nFkEJmAXBib99tE5N7Ake2OO7rzZGhQSnGcXjhcYu1YOd7rwLKkHecqU8m4bFBjY9CBztbFRsRT883DFi7

curl.exe -X POST "https://printer-backend-934564650450.europe-west1.run.app/upload" `
  -F "file=@C:\Users\andre\Downloads\Cube.3mf" `
  --form-string recipient_id=user-123 `
  --form-string 'unencrypted_data={"printJob":"demo"}' `
  --form-string 'encrypted_data_payload={"secret":"1234"}'

curl.exe -X POST "https://printer-backend-934564650450.europe-west1.run.app/upload" `
  -F "file=@C:\Users\508484\Downloads\googleting.gcode.3mf" `
  --form-string recipient_id=user-123 `
  --form-string 'unencrypted_data={"printJob":"demo"}' `
  --form-string 'encrypted_data_payload={"secret":"1234"}'

## Local development

### Backend server

1. Opprett et virtuelt miljø og installer avhengighetene:

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. Sett nødvendige miljøvariabler for Google Cloud (erstatt med dine egne verdier):

   ```bash
   export GCP_PROJECT_ID="<prosjekt-id>"
   export GCS_BUCKET_NAME="<bucket>"
   export KMS_KEY_RING="<keyring>"
   export KMS_KEY_NAME="<key>"
   export KMS_LOCATION="<region>"
   export FIRESTORE_COLLECTION_FILES="print_jobs"
   export FIRESTORE_COLLECTION_PRINTER_STATUS="printer_telemetry"
   ```

3. Start utviklingsserveren lokalt:

   ```bash
   flask --app main run --debug
   ```

   Serveren lytter som standard på `http://127.0.0.1:5000`.

### Lokal PC-klient

Installer klientavhengigheter (bruk gjerne det samme virtuelle miljøet):

```bash
pip install -r requirements.txt
```

Tilgjengelige kommandoer:

- **Hente én fil via token**:

  ```bash
  python client/client.py fetch --baseUrl http://127.0.0.1:5000 --fetchToken <token> --outputDir ./nedlastinger
  ```

- **Lytte etter jobber for en bestemt mottaker** (klienten henter automatisk nye filer for valgt `recipientId`):

  ```bash
  python client/client.py listen --baseUrl http://127.0.0.1:5000 --recipientId user-123 --outputDir ./nedlastinger --pollInterval 30
  ```

- **Sende statusoppdateringer**:

  ```bash
  python client/client.py status --baseUrl http://127.0.0.1:5000 --apiKey <api-nokkel> --printerSerial PRN-001 --interval 60 --numUpdates 5
  ```

Klienten bruker `listen`-kommandoen til å velge hvilken mottaker den skal overvåke og laster automatisk ned alle filer som er tildelt den valgte mottakeren.

