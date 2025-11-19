# Cloud Printer Backend API Documentation

## Overview

The Cloud Printer Backend is a Flask-based API hosted on Google Cloud Run that manages 3D printer jobs, file uploads, printer commands, and status updates. It integrates with Google Cloud Firestore for data persistence and Google Cloud Storage for file management.

**Production Base URLs:**
- `https://printpro3d-api-931368217793.europe-west1.run.app` – Primary public endpoint. Use this for all new frontend, partner, or mobile integrations so you stay on the supported PrintPro3D host.
- `https://printer-backend-934564650450.europe-west1.run.app` – Legacy endpoint that remains available for the LAN client and other internal integrations until they are redeployed.

---

## Table of Contents

1. [Architecture](#architecture)
2. [Authentication](#authentication)
3. [API Endpoints](#api-endpoints)
   - [File Management](#file-management)
   - [Product Endpoints](#product-endpoints)
   - [Job Management](#job-management)
   - [Printer Control](#printer-control)
   - [Status Updates](#status-updates)
   - [Debug Endpoints](#debug-endpoints)
4. [Data Models](#data-models)
5. [Error Handling](#error-handling)
6. [Environment Configuration](#environment-configuration)

---

## Architecture

The system follows a distributed architecture with three main components:

```
┌─────────────┐      HTTPS       ┌──────────────────┐    Firestore    ┌────────────────┐
│   Clients   │ ───────────────▶ │  Cloud Run API   │ ──────────────▶ │  Firestore DB  │
│  (Web/App)  │                  │   (main.py)      │                 │  Collections   │
└─────────────┘                  └──────────────────┘                 └────────────────┘
                                          │                                     │
                                          │                                     ▼
                                          │                    - files
                                          │                    - printer_commands
                                          │                    - printer_status_updates
                                          ▼                    - print_jobs
                                  ┌──────────────┐
                                  │   GCS Bucket │
                                  │  File Storage│
                                  └──────────────┘
                                          │
                                          │ MQTT/HTTP (LAN)
                                          ▼
                                  ┌──────────────┐
                                  │ Bambu Printer│
                                  │  (LAN Client)│
                                  └──────────────┘
```

### Typical Command Flow

1. External client calls `POST /control` to create a printer command
2. API writes document to `printer_commands` collection with status `pending`
3. LAN client polls Firestore and retrieves pending commands
4. Client executes command on printer over LAN
5. Client updates Firestore document to `completed` or `failed`

---

## Authentication

The API uses API key authentication via the `X-API-Key` header or `apiKey` query parameter.

### Configuration

API keys can be configured via:
- **Environment variable:** `API_KEYS_PRINTER_STATUS` (comma-separated keys)
- **Secret Manager:** `SECRET_MANAGER_API_KEYS_PATH` or `SECRET_MANAGER_API_KEYS`

### Example Request

```bash
curl -X POST https://printpro3d-api-931368217793.europe-west1.run.app/control \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key-here" \
  -d '{"recipientId":"RID123","commandType":"pause"}'
```

---

## API Endpoints

### File Management

#### 1. Upload File
**POST** `/upload`

Uploads a file (G-code or 3MF) to cloud storage and creates metadata in Firestore.

**Headers:**
- `Content-Type: multipart/form-data` or `application/json`
- `X-API-Key: <api-key>`

**Form Fields (multipart/form-data):**
```
recipientId: string (required) - Unique recipient identifier
productId: string (required) - Product identifier
file: binary (optional) - File upload
gcodeUrl: string (optional) - URL to G-code file
encryptedData: string (optional) - Encrypted metadata
unencryptedData: string (optional) - Plaintext metadata
```

**JSON Body (alternative):**
```json
{
  "recipientId": "RID123",
  "productId": "PROD-55",
  "gcodeUrl": "https://storage.googleapis.com/bucket/file.gcode",
  "encryptedData": "base64-encrypted-string",
  "unencryptedData": "{\"printSettings\":\"...\"}"
}
```

**Response (200):**
```json
{
  "ok": true,
  "fileId": "file-uuid-1234",
  "productId": "PROD-55",
  "recipientId": "RID123",
  "fetchToken": "token-abcd1234",
  "expiresAt": "2025-11-01T12:00:00Z",
  "gcsBucket": "your-bucket",
  "gcsPath": "products/PROD-55/file.gcode"
}
```

**Allowed File Types:**
- Extensions: `.3mf`, `.gcode`, `.gco`
- MIME types: `application/octet-stream`, `application/x-gcode`, `text/plain`, `model/3mf`

---

#### 2. Fetch File
**GET** `/fetch/<fetchToken>`

Retrieves file data and metadata using a one-time fetch token.

**URL Parameters:**
- `fetchToken` - Token provided by upload or handshake endpoint

**Response (200):**
```json
{
  "ok": true,
  "fileId": "file-uuid-1234",
  "recipientId": "RID123",
  "productId": "PROD-55",
  "fetchToken": "token-abcd1234",
  "data": "base64-encoded-file-content",
  "fileName": "model.3mf",
  "metadata": {
    "encryptedData": "...",
    "unencryptedData": "..."
  }
}
```

**Notes:**
- Token is consumed after first use
- Token expires based on `FETCH_TOKEN_TTL_MINUTES` (default: 15 minutes)
- Supports automatic decryption via KMS if `encryptedData` present

---

### Product Endpoints

#### 3. Product Handshake
**POST** `/products/<productId>/handshake`

Initiates a handshake for a product, generating a fresh fetch token for file retrieval.

**URL Parameters:**
- `productId` - Product identifier

**Headers:**
- `X-API-Key: <api-key>`

**Request Body:**
```json
{
  "recipientId": "RID123"
}
```

**Response (200):**
```json
{
  "ok": true,
  "productId": "PROD-55",
  "recipientId": "RID123",
  "fetchToken": "new-token-xyz789",
  "expiresAt": "2025-11-01T12:15:00Z",
  "hasFiles": true
}
```

**Use Case:**
- Two-phase handshake for deduplication
- Prevents duplicate file downloads
- Client calls handshake first, receives token, then fetches file

---

#### 4. Product Status Update
**POST** `/products/<productId>/status`

Updates the status of a product/print job.

**URL Parameters:**
- `productId` - Product identifier

**Headers:**
- `X-API-Key: <api-key>`

**Request Body:**
```json
{
  "recipientId": "RID123",
  "requestedMode": "remote",
  "success": true,
  "fileName": "model.3mf",
  "lastRequestedAt": "2025-10-31T10:30:00Z",
  "errorMessage": null,
  "printStarted": true,
  "printCompleted": false
}
```

**Response (200):**
```json
{
  "ok": true,
  "productId": "PROD-55",
  "status": "printing"
}
```

---

### Job Management

#### 5. List Pending Jobs
**POST** `/api/apps/<appId>/functions/listPendingJobs`

Retrieves pending print jobs for a specific recipient.

**URL Parameters:**
- `appId` - Application identifier

**Headers:**
- `X-API-Key: <api-key>`

**Request Body:**
```json
{
  "recipientId": "RID123"
}
```

**Response (200):**
```json
{
  "ok": true,
  "pending": [
    {
      "fileId": "file-uuid-1234",
      "productId": "PROD-55",
      "fileName": "model.3mf",
      "fetchToken": "token-abcd1234",
      "createdAt": "2025-10-31T09:00:00Z"
    }
  ],
  "skipped": []
}
```

---

#### 6. List Recipient Files (Legacy)
**POST** `/api/apps/<appId>/functions/listRecipientFiles`

Legacy endpoint that redirects to `listPendingJobs`. Maintained for backward compatibility.

---

#### 7. Claim Job
**POST** `/api/apps/<appId>/functions/claimJob`

Claims a pending job, transitioning it to "in-progress" status.

**URL Parameters:**
- `appId` - Application identifier

**Headers:**
- `X-API-Key: <api-key>`

**Request Body:**
```json
{
  "recipientId": "RID123",
  "printerId": "printer-001",
  "jobId": "file-uuid-1234"
}
```

**Response (200):**
```json
{
  "ok": true,
  "jobId": "file-uuid-1234",
  "status": "claimed",
  "claimedBy": "printer-001",
  "claimedAt": "2025-10-31T10:00:00Z"
}
```

**Claimable Statuses:**
- `uploaded`
- `queued`
- `pending`

---

#### 8. List Pending Files
**GET** `/recipients/<recipientId>/pending`

Lists all pending files for a recipient (no authentication required).

**URL Parameters:**
- `recipientId` - Recipient identifier

**Response (200):**
```json
{
  "ok": true,
  "pending": [
    {
      "fileId": "file-uuid-1234",
      "productId": "PROD-55",
      "fileName": "model.3mf",
      "fetchToken": "token-abcd1234",
      "status": "pending"
    }
  ],
  "skipped": []
}
```

---

### Printer Control

#### 9. Queue Printer Control Command
**POST/GET** `/control`

Creates or lists printer control commands.

**POST - Queue Command**

**Headers:**
- `Content-Type: application/json`
- `X-API-Key: <api-key>`

**Request Body:**
```json
{
  "recipientId": "RID123",
  "printerSerial": "01P00A381200434",
  "commandType": "pause",
  "metadata": {
    "reason": "filament change"
  },
  "expiresAt": "2025-10-31T15:00:00Z",
  "printerIpAddress": "192.168.1.100",
  "printerId": "printer-001"
}
```

**Supported Command Types:**
- `pause` - Pause current print
- `resume` - Resume paused print
- `stop` / `cancel` - Stop/cancel print
- `heat` / `setHeat` - Set temperatures
- `cool` / `cooldown` - Cool down printer
- `start_print` - Start a print job
- `camera` / `camera_on` / `camera_off` - Control camera
- `set_speed` / `speed` - Set print speed
- `set_fan` / `fan` - Set fan speed
- `home` - Home axes
- `light_on` / `light_off` - Toggle chamber light
- `load_filament` / `unload_filament` - Filament operations
- `move` / `jog` - Manual axis movement
- `sendGcode` - Send raw G-code

**Response (200):**
```json
{
  "ok": true,
  "commandId": "cmd-uuid-5678",
  "status": "pending",
  "createdAt": "2025-10-31T10:00:00Z"
}
```

**GET - List Pending Commands**

**Query Parameters:**
- `recipientId` - Filter by recipient
- `printerSerial` - Filter by printer serial
- `limit` - Max results (default: 25)

**Response (200):**
```json
{
  "ok": true,
  "commands": [
    {
      "commandId": "cmd-uuid-5678",
      "commandType": "pause",
      "status": "pending",
      "recipientId": "RID123",
      "createdAt": "2025-10-31T10:00:00Z"
    }
  ]
}
```

---

#### 10. Acknowledge Command
**POST** `/control/ack`

Acknowledges receipt of a command by the client.

**Headers:**
- `X-API-Key: <api-key>`

**Request Body:**
```json
{
  "commandId": "cmd-uuid-5678",
  "recipientId": "RID123",
  "printerSerial": "01P00A381200434",
  "status": "processing"
}
```

**Response (200):**
```json
{
  "ok": true,
  "commandId": "cmd-uuid-5678",
  "status": "processing",
  "acknowledgedAt": "2025-10-31T10:00:30Z"
}
```

---

#### 11. Submit Command Result
**POST** `/control/result`

Submits the final result of a command execution.

**Headers:**
- `X-API-Key: <api-key>`

**Request Body:**
```json
{
  "commandId": "cmd-uuid-5678",
  "recipientId": "RID123",
  "printerSerial": "01P00A381200434",
  "status": "completed",
  "message": "Print paused successfully",
  "errorMessage": null
}
```

**Response (200):**
```json
{
  "ok": true,
  "commandId": "cmd-uuid-5678",
  "status": "completed",
  "completedAt": "2025-10-31T10:01:00Z"
}
```

**Valid Result Statuses:**
- `completed` - Command executed successfully
- `failed` - Command failed
- `error` - Error during execution

---

### Status Updates

#### 12. Update Printer Status (App-Based)
**POST** `/api/apps/<appId>/functions/updatePrinterStatus`

Receives status updates from printers for a specific app context.

**URL Parameters:**
- `appId` - Application identifier

**Headers:**
- `X-API-Key: <api-key>`

**Request Body:**
```json
{
  "recipientId": "RID123",
  "printerSerial": "01P00A381200434",
  "printerIpAddress": "192.168.1.100",
  "status": {
    "state": "printing",
    "progress": 45,
    "bedTemp": 60,
    "nozzleTemp": 220,
    "remainingTime": 3600,
    "gcodeState": "RUNNING",
    "amsStatus": "idle"
  },
  "timestamp": "2025-10-31T10:05:00Z"
}
```

**Response (200):**
```json
{
  "ok": true,
  "statusUpdateId": "status-uuid-9012",
  "timestamp": "2025-10-31T10:05:00Z"
}
```

---

#### 13. Printer Status Update (Default)
**POST** `/printer-status`

Generic printer status update endpoint without app context.

**Headers:**
- `X-API-Key: <api-key>`

**Request Body:** Same as app-based endpoint

**Response:** Same as app-based endpoint

---

### Debug Endpoints

#### 14. Debug List Pending Commands
**POST** `/debug/listPendingCommands`

Debug endpoint for inspecting pending commands for a recipient.

**Headers:**
- `X-API-Key: <api-key>`

**Request Body:**
```json
{
  "recipientId": "RID123",
  "limit": 50
}
```

**Response (200):**
```json
{
  "ok": true,
  "count": 3,
  "commands": [
    {
      "commandId": "cmd-uuid-5678",
      "commandType": "pause",
      "status": "pending",
      "recipientId": "RID123",
      "printerSerial": "01P00A381200434",
      "metadata": {},
      "createdAt": "2025-10-31T10:00:00Z",
      "expiresAt": "2025-10-31T15:00:00Z"
    }
  ]
}
```

---

#### 15. Health Check
**GET** `/`

Basic health check endpoint.

**Response (200):**
```json
{
  "ok": true,
  "message": "Cloud printer backend is running"
}
```

---

## Data Models

### Firestore Collections

#### `files`
```json
{
  "fileId": "string (auto-generated UUID)",
  "recipientId": "string (required)",
  "productId": "string (required)",
  "fileName": "string",
  "status": "uploaded | claimed | printing | completed | failed",
  "gcsBucket": "string",
  "gcsPath": "string",
  "fetchToken": "string (hashed)",
  "fetchTokenExpiry": "timestamp",
  "fetchTokenConsumed": "boolean",
  "encryptedData": "string (base64)",
  "unencryptedData": "string (JSON)",
  "createdAt": "timestamp (auto)",
  "claimedBy": "string (optional)",
  "claimedAt": "timestamp (optional)"
}
```

#### `printer_commands`
```json
{
  "commandId": "string (auto-generated UUID)",
  "recipientId": "string (required)",
  "printerSerial": "string (optional)",
  "printerIpAddress": "string (optional)",
  "printerId": "string (optional)",
  "commandType": "string (required)",
  "metadata": "object (optional)",
  "status": "pending | processing | completed | failed",
  "message": "string (optional)",
  "errorMessage": "string (optional)",
  "createdAt": "timestamp (auto)",
  "acknowledgedAt": "timestamp (optional)",
  "completedAt": "timestamp (optional)",
  "expiresAt": "timestamp (optional)"
}
```

#### `printer_status_updates`
```json
{
  "updateId": "string (auto-generated UUID)",
  "recipientId": "string",
  "printerSerial": "string",
  "printerIpAddress": "string",
  "status": "object (printer state)",
  "timestamp": "timestamp",
  "createdAt": "timestamp (auto)"
}
```

### Composite Index Requirements

For efficient querying, create the following Firestore composite index:

**Index:** `printer_commands`
- `recipientId` (ASCENDING)
- `status` (ASCENDING)
- `createdAt` (DESCENDING)

**Deploy via:**
```bash
firebase deploy --only firestore:indexes
```

Or upload `firestore.indexes.json`:
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
  ]
}
```

---

## Error Handling

All error responses follow this format:

```json
{
  "ok": false,
  "error_type": "ValidationError | AuthError | NotFoundError | InternalError",
  "message": "Human-readable error message",
  "detail": "Additional error details",
  "traceback": "Stack trace (if available)"
}
```

### Common Error Codes

| Status Code | Error Type | Description |
|-------------|------------|-------------|
| 400 | ValidationError | Invalid request payload or missing required fields |
| 401 | AuthError | Invalid or missing API key |
| 403 | Forbidden | Insufficient permissions |
| 404 | NotFoundError | Resource not found (file, command, etc.) |
| 409 | ConflictError | Resource conflict (e.g., job already claimed) |
| 500 | InternalError | Server error or database failure |

### Firestore Index Error

If you receive an error about missing Firestore indexes, the response will include:

```json
{
  "ok": false,
  "error_type": "FirestoreQueryError",
  "message": "The query requires an index",
  "indexUrl": "https://console.firebase.google.com/...",
  "detail": "Follow the URL to create the required index"
}
```

---

## Environment Configuration

### Required Environment Variables

```bash
# GCP Project Configuration
FIRESTORE_PROJECT_ID=print-pipe-demo
# or
GCP_PROJECT_ID=print-pipe-demo

# Service Account (for local development)
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json

# Cloud Storage
GCS_BUCKET_NAME=your-printer-files-bucket

# KMS for Encryption (optional)
KMS_KEY_PATH=projects/PROJECT/locations/LOCATION/keyRings/RING/cryptoKeys/KEY

# API Keys
API_KEYS_PRINTER_STATUS=key1,key2,key3
# or
SECRET_MANAGER_API_KEYS_PATH=projects/PROJECT/secrets/api-keys/versions/latest

# Server Configuration
PORT=8080
```

### Optional Environment Variables

```bash
# Fetch Token TTL
FETCH_TOKEN_TTL_MINUTES=15

# Firestore Collection Names (override defaults)
FIRESTORE_COLLECTION_FILES=files
FIRESTORE_COLLECTION_PRINTER_STATUS=printer_status_updates
FIRESTORE_COLLECTION_PRINTER_COMMANDS=printer_commands
```

---

## Google Cloud Backend Integration

The system uses a self-hosted Google Cloud infrastructure for print job management. The backend is deployed on Google Cloud Run with a PostgreSQL database.

### 1. Authentication
- API key is stored as `PRINTER_API_KEY` in Secret Manager
- All requests to `https://printpro3d-api-931368217793.europe-west1.run.app` include `X-API-Key: <PRINTER_API_KEY>`
- Content-Type header: `application/json`

### 2. Create Job Requisition
**POST** `https://printpro3d-api-931368217793.europe-west1.run.app/api/print-jobs`
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

**Response:**
```json
{
  "jobId": "job-abc-123",
  "productId": "PROD-55",
  "status": "pending",
  "files": [
    {"url": "...", "type": "gcode"},
    {"url": "...", "type": "thumbnail"}
  ]
}
```

### 3. File Synchronization
- Create `download` command in `printer_commands` for each file
- LAN client downloads and stores checksum
- Update command status to `completed`

### 4. Start Print
- Cloud Run creates a `start` command in `printer_commands`
- LAN client picks up the command and starts the print locally

### 5. Status Reporting
**POST** `https://printpro3d-api-931368217793.europe-west1.run.app/api/printer-status`
```json
{
  "recipientId": "RID123",
  "printerIpAddress": "192.168.1.100",
  "progressPercent": 45,
  "bedTemp": 60,
  "nozzleTemp": 220,
  "lastEventAt": "2025-10-31T10:05:00Z"
}
```

All timestamps use ISO 8601 format (`YYYY-MM-DDTHH:MM:SSZ`).

---

## Deployment

### Cloud Run Deployment

```bash
# Build and deploy
gcloud run deploy printer-backend \
  --source . \
  --platform managed \
  --region europe-west1 \
  --allow-unauthenticated \
  --set-env-vars FIRESTORE_PROJECT_ID=print-pipe-demo \
  --set-secrets API_KEYS_PRINTER_STATUS=printer-api-keys:latest
```

### Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables
export FIRESTORE_PROJECT_ID=print-pipe-demo
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
export API_KEYS_PRINTER_STATUS=dev-key-123

# Run Flask app
python main.py
```

The API will be available at `http://localhost:8080`

---

## Testing

### Example: Upload and Fetch Workflow

```bash
# 1. Upload a file
curl -X POST http://localhost:8080/upload \
  -H "X-API-Key: dev-key-123" \
  -F "recipientId=RID123" \
  -F "productId=PROD-55" \
  -F "file=@model.3mf"

# Response: {"ok":true,"fetchToken":"token-xyz","fileId":"file-123",...}

# 2. Fetch the file
curl http://localhost:8080/fetch/token-xyz

# Response: {"ok":true,"data":"base64-content",...}
```

### Example: Control Command Flow

```bash
# 1. Queue a pause command
curl -X POST http://localhost:8080/control \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-key-123" \
  -d '{
    "recipientId":"RID123",
    "printerSerial":"01P00A381200434",
    "commandType":"pause"
  }'

# Response: {"ok":true,"commandId":"cmd-456","status":"pending"}

# 2. Client acknowledges
curl -X POST http://localhost:8080/control/ack \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-key-123" \
  -d '{
    "commandId":"cmd-456",
    "recipientId":"RID123",
    "printerSerial":"01P00A381200434",
    "status":"processing"
  }'

# 3. Client submits result
curl -X POST http://localhost:8080/control/result \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-key-123" \
  -d '{
    "commandId":"cmd-456",
    "recipientId":"RID123",
    "printerSerial":"01P00A381200434",
    "status":"completed",
    "message":"Print paused successfully"
  }'
```

---

## Support

For issues or questions:
- Check logs in Google Cloud Console
- Review Firestore data for debugging
- Ensure all required indexes are deployed
- Verify API keys are configured correctly

## License

[Specify your license here]
