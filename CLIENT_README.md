# PrintMaster Client Documentation

## Overview

The PrintMaster Client is a Python-based application that interfaces with Bambu Lab 3D printers and the Cloud Run backend. It manages file downloads, automatic print dispatch, printer command execution, status monitoring, and remote communication.

**Key Features:**
- 🖨️ Direct integration with Bambu Lab printers (X1C, P1S, etc.)
- ☁️ Cloud-based job management with automatic polling
- 🎯 Automatic printer assignment and job dispatch
- 📡 Real-time status monitoring and reporting
- 🎮 Remote printer control (pause, resume, temperature control, etc.)
- 📸 Periodic camera snapshot capture
- 🖥️ Desktop GUI for easy management
- 🔄 Multi-threaded architecture for concurrent operations

---

## Table of Contents

1. [Installation](#installation)
2. [Quick Start](#quick-start)
3. [Commands](#commands)
4. [Configuration](#configuration)
5. [Architecture](#architecture)
6. [Modules](#modules)
7. [Workflows](#workflows)
8. [Troubleshooting](#troubleshooting)

---

## Installation

### Prerequisites

- Python 3.8 or higher
- Network access to Bambu Lab printers (LAN or Bambu Connect)
- Access to the Cloud Run backend API
- API keys for authentication

### Install Dependencies

```bash
cd client
pip install -r requirements.txt
```

### Required Python Packages

- `requests` - HTTP client for API communication
- `bambulabs_api` - Official Bambu Lab printer SDK
- `tkinter` - GUI framework (usually included with Python)
- `Pillow` - Image processing for camera snapshots
- `sqlite3` - Local database (included with Python)

---

## Quick Start

> **API base URLs**
> - `https://printpro3d-api-931368217793.europe-west1.run.app` – Default for new frontend flows, partner integrations, and any manual testing snippets you copy from this document.
> - `https://printer-backend-934564650450.europe-west1.run.app` – Legacy host that remains live for the LAN client and other internal scripts until they are redeployed.

### 1. Initialize Configuration

First run will create the configuration directory:

```bash
python -m client.client listen --recipientId YOUR_RECIPIENT_ID --baseUrl https://printpro3d-api-931368217793.europe-west1.run.app
```

This creates `~/.printmaster/` with initial configuration files.

### 2. Configure Your Printer

Edit `~/.printmaster/printers.json`:

```json
[
  {
    "serialNumber": "00M201231234567",
    "nickname": "My P1S",
    "brand": "Bambu Lab",
    "ipAddress": "192.168.1.100",
    "accessCode": "0123456789AB",
    "transport": "lan",
    "useCloud": false,
    "useAms": true,
    "bedLeveling": true,
    "layerInspect": false
  }
]
```

**How to find your printer details:**
- **IP Address:** Check your router's DHCP table or printer display
- **Access Code:** Found in printer settings (12-character hex code)
- **Serial Number:** Located on the printer label or in Bambu Studio

### 3. Start Listening for Jobs

```bash
python -m client.client listen \
  --recipientId YOUR_RECIPIENT_ID \
  --baseUrl https://printpro3d-api-931368217793.europe-west1.run.app \
  --outputDir ~/.printmaster/files
```

The client will now:
- Poll the backend for new print jobs
- Automatically download files
- Dispatch to the appropriate printer
- Monitor print progress
- Report status back to the cloud

---

## Commands

### FETCH Command

Download a single file using a fetch token.

```bash
python -m client.client fetch \
  --fetchToken <token> \
  --baseUrl https://printpro3d-api-931368217793.europe-west1.run.app \
  --outputDir ~/.printmaster/files
```

**Options:**
- `--fetchToken` - Token provided by the web app or API
- `--baseUrl` - Backend API URL (default: production URL)
- `--outputDir` - Directory to save downloaded files
- `--mode` - `remote` (default) or `offline` for testing
- `--metadataFile` - Path to JSON metadata (offline mode)
- `--dataFile` - Path to file contents (offline mode)

**Example:**
```bash
python -m client.client fetch \
  --fetchToken abc123xyz789 \
  --outputDir /home/user/prints
```

---

### STATUS Command

Send periodic status updates to the backend (for testing).

```bash
python -m client.client status \
  --apiKey YOUR_API_KEY \
  --printerSerial 01P00A381200434 \
  --baseUrl https://printpro3d-api-931368217793.europe-west1.run.app \
  --interval 60 \
  --numUpdates 10
```

**Options:**
- `--apiKey` - API key for authentication (required)
- `--printerSerial` - Printer serial number (required)
- `--baseUrl` - Backend API URL
- `--interval` - Seconds between updates (default: 60)
- `--numUpdates` - Number of updates to send (0 = indefinite)
- `--recipientId` - Optional recipient identifier

**Example:**
```bash
python -m client.client status \
  --apiKey mykey123 \
  --printerSerial 00M201231234567 \
  --interval 30 \
  --numUpdates 0
```

---

### LISTEN Command

Continuously poll for new jobs and auto-dispatch to printers.

```bash
python -m client.client listen \
  --recipientId YOUR_RECIPIENT_ID \
  --baseUrl https://printpro3d-api-931368217793.europe-west1.run.app \
  --pollInterval 15 \
  --outputDir ~/.printmaster/files
```

**Options:**
- `--recipientId` - Your unique recipient ID (required)
- `--baseUrl` - Backend API URL
- `--outputDir` - Directory for downloaded files
- `--pollInterval` - Seconds between polls (default: 30)
- `--maxIterations` - Max poll cycles (0 = indefinite)
- `--mode` - `remote` or `offline`
- `--offlineDataset` - JSON file for offline testing
- `--logFile` - Path to JSON log file

**Example:**
```bash
python -m client.client listen \
  --recipientId RID123ABC \
  --pollInterval 10 \
  --outputDir ~/printer_jobs \
  --logFile ~/printmaster.log
```

---

## Configuration

### Directory Structure

```
~/.printmaster/
├── printers.json              # Printer configurations
├── client-info.json           # Client recipient ID
├── printmaster.db             # SQLite job database
├── product-records.json       # Product fetch tracking
├── command-cache.json         # Command dedup cache
├── listener-log.json          # File fetch history
├── files/                     # Downloaded job files
│   ├── job1.3mf
│   ├── job2.gcode
│   └── ...
├── camera/                    # Camera snapshots by date
│   ├── 2025-10-31/
│   │   ├── printer1-snapshot1.jpg
│   │   └── ...
│   └── ...
└── timelapse/                 # Timelapse video frames
    └── ...
```

---

### Printer Configuration (`printers.json`)

Full configuration options:

```json
[
  {
    "serialNumber": "00M201231234567",
    "nickname": "Office P1S",
    "brand": "Bambu Lab",
    "model": "P1S",
    "ipAddress": "192.168.1.100",
    "accessCode": "0123456789AB",
    "transport": "lan",
    "connectionMethod": "lan",
    "useCloud": false,

    "useAms": true,
    "bedLeveling": true,
    "layerInspect": false,
    "flowCalibration": false,
    "vibrationCalibration": false,

    "enableTimeLapse": false,
    "timeLapseDirectory": "/home/user/.printmaster/timelapse",

    "enableBrakePlate": false,
    "plateTemplate": null,
    "plateIndex": null,

    "secureConnection": false,
    "cloudUrl": null,
    "cloudTimeout": 180,
    "waitSeconds": 8,
    "lanStrategy": "legacy"
  }
]
```

**Configuration Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `serialNumber` | string | Unique printer serial (required) |
| `nickname` | string | Human-readable name |
| `brand` | string | Manufacturer (e.g., "Bambu Lab") |
| `model` | string | Printer model (e.g., "P1S", "X1C") |
| `ipAddress` | string | LAN IP address |
| `accessCode` | string | 12-character printer access code |
| `transport` | string | `"lan"` or `"bambu_connect"` |
| `useCloud` | boolean | Use Bambu Connect cloud service |
| `useAms` | boolean | Enable automatic material system |
| `bedLeveling` | boolean | Enable bed leveling before print |
| `layerInspect` | boolean | Enable layer inspection |
| `flowCalibration` | boolean | Enable flow calibration |
| `vibrationCalibration` | boolean | Enable vibration calibration |
| `enableTimeLapse` | boolean | Capture timelapse frames |
| `timeLapseDirectory` | string | Path for timelapse storage |
| `enableBrakePlate` | boolean | Enable brake plate support |
| `plateIndex` | number | Default build plate index |
| `cloudTimeout` | number | Timeout for cloud operations (seconds) |
| `waitSeconds` | number | Wait time before starting print |

---

### Environment Variables

Configure via environment variables or `.env` file:

```bash
# Backend Configuration
PRINTER_BACKEND_BASE_URL="https://printpro3d-api-931368217793.europe-west1.run.app"
# Legacy fallback (only if an integration cannot reach the PrintPro3D host yet):
# PRINTER_BACKEND_BASE_URL="https://printer-backend-934564650450.europe-west1.run.app"
PRINTER_BACKEND_API_KEY="your-api-key"

# Base44 Integration
BASE44_API_BASE="https://printpro3d-api-931368217793.europe-west1.run.app"
# Legacy fallback:
# BASE44_API_BASE="https://printer-backend-934564650450.europe-west1.run.app"
BASE44_FUNCTIONS_API_KEY="your-functions-key"
BASE44_API_KEY="fallback-key"

# Client Identity
BASE44_RECIPIENT_ID="RID123ABC"

# Command Polling
CONTROL_POLL_SEC=15              # Poll interval (default: 15s)
CONTROL_POLL_MODE="recipient"    # "recipient" or "printer"

# Camera Configuration
CAMERA_SNAPSHOT_INTERVAL_SECONDS=30
PRINTMASTER_CAMERA_DEBUG=false

# Debug Flags
PRINTMASTER_STATUS_DEBUG=false
PRINTMASTER_START_DEBUG=false

# FTPS Configuration
BAMBU_FTPS_REACTIVATE_STOR_COMMANDS="ENABLE_STOR"
```

---

## Architecture

### Component Overview

```
┌─────────────────────────────────────────────────────────┐
│                   PrintMaster Client                    │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │   CLI       │  │     GUI      │  │  Database    │  │
│  │ (client.py) │  │   (gui.py)   │  │(database.py) │  │
│  └──────┬──────┘  └──────┬───────┘  └──────┬───────┘  │
│         │                │                  │          │
│         └────────────────┴──────────────────┘          │
│                          │                             │
│         ┌────────────────┴────────────────┐            │
│         ▼                                 ▼            │
│  ┌──────────────┐                 ┌──────────────┐    │
│  │ File Fetcher │                 │   Command    │    │
│  │ (client.py)  │                 │  Controller  │    │
│  └──────┬───────┘                 └──────┬───────┘    │
│         │                                 │            │
│         │         ┌───────────────────────┘            │
│         │         │                                    │
│         ▼         ▼                                    │
│  ┌─────────────────────────────┐                      │
│  │   Bambu Printer Interface   │                      │
│  │     (bambuPrinter.py)       │                      │
│  └─────────────┬───────────────┘                      │
│                │                                       │
└────────────────┼───────────────────────────────────────┘
                 │
                 │ MQTT / FTPS / HTTP
                 ▼
         ┌──────────────┐
         │ Bambu Printer│
         │  (X1C, P1S)  │
         └──────────────┘
```

### Threading Model

The client uses multiple threads for concurrent operations:

1. **Main Thread:** CLI/GUI event loop
2. **File Listener Thread:** Polls backend for new jobs
3. **Command Worker Threads:** One per printer/recipient
4. **Status Monitor Threads:** One per printer
5. **Camera Capture Threads:** One per printer

---

## Modules

### 1. `client.py` (Main Orchestrator)

**Responsibilities:**
- Command-line interface and argument parsing
- Backend API communication
- File download and metadata extraction
- Printer configuration management
- Print job dispatch coordination

**Key Functions:**
- `listenForFiles()` - Main polling loop for recipient
- `performFetch()` - Download single file with token
- `dispatchBambuPrintIfPossible()` - Send job to printer
- `resolvePrinterDetails()` - Match job to configured printer
- `upsertPrinterFromJob()` - Auto-update printer config

---

### 2. `bambuPrinter.py` (Printer Interface)

**Responsibilities:**
- Direct printer communication
- File upload (FTPS or Bambu API)
- Print job initiation
- 3MF modification (skipped objects)
- Status reporting

**Key Functions:**
- `sendBambuPrintJob()` - Main print dispatcher
- `uploadViaFtps()` - FTPS file upload
- `uploadViaBambulabsApi()` - API file upload
- `startPrintViaApi()` - Initiate print with acknowledgment
- `applySkippedObjectsToArchive()` - Modify 3MF slice config
- `deleteRemoteFile()` - Cleanup after print

**Supported Transport Methods:**
- **LAN:** Direct MQTT/FTPS over local network
- **Bambu Connect:** Cloud relay through Bambu servers

---

### 3. `command_controller.py` (Command Execution)

**Responsibilities:**
- Poll backend for control commands
- Execute commands on printers
- Monitor printer status
- Capture camera snapshots
- Report command results

**Key Classes:**
- `CommandWorker` - Handles commands for single printer
- `RecipientCommandRouter` - Routes commands to multiple printers

**Supported Commands:**

| Category | Commands |
|----------|----------|
| **Print Control** | `pause`, `resume`, `stop`, `cancel`, `start_print` |
| **Temperature** | `heat`, `setHeat`, `cool`, `cooldown` |
| **Camera** | `camera`, `camera_on`, `camera_off` |
| **Speed/Fan** | `set_speed`, `speed`, `set_fan`, `fan` |
| **Lighting** | `light_on`, `light_off` |
| **Filament** | `load_filament`, `unload_filament` |
| **Movement** | `home`, `move`, `jog` |
| **G-code** | `sendGcode` |

---

### 4. `status_subscriber.py` (Status Monitoring)

**Responsibilities:**
- Maintain persistent connections to printers
- Stream real-time status updates
- Detect errors and filament conflicts
- Report aggregated status to backend

**Key Class:**
- `BambuStatusSubscriber` - Manages multiple printer connections

**Monitored Metrics:**
- Print progress percentage
- Bed and nozzle temperatures
- Print time remaining
- G-code state (idle, running, paused)
- AMS status and filament conflicts
- HMS error codes

---

### 5. `database.py` (Local Storage)

**Responsibilities:**
- SQLite database management
- Job history tracking
- Printer registry
- Metadata caching

**Database Tables:**
- `printers` - Registered printers
- `jobs` - Print job history
- `jobMetadata` - Encrypted/decrypted job data
- `products` - Product tracking for deduplication

---

### 6. `gui.py` (Desktop Interface)

**Responsibilities:**
- User-friendly desktop application
- Printer configuration UI
- Command launching (listen/status)
- Log viewing
- System tray integration

**Features:**
- Recipient ID generation and display
- Printer list management
- Background worker status
- JSON log viewer
- Copy-to-clipboard for recipient ID

---

### 7. `base44_client.py` (API Client)

**Responsibilities:**
- Low-level HTTP communication with backend
- Request/response handling
- Error handling and retries

**API Functions:**
- `listPendingCommandsForRecipient()` - Get pending commands
- `acknowledgeCommand()` - ACK command receipt
- `postCommandResult()` - Report command completion
- `postUpdateStatus()` - Send printer status
- `postReportError()` - Report errors
- `postReportPrinterImage()` - Upload camera snapshots

---

## Workflows

### Workflow 1: Automatic Print Dispatch

```
1. Client polls /recipients/{recipientId}/pending
   ↓
2. Backend returns pending jobs with fetch tokens
   ↓
3. Client performs two-phase handshake (optional)
   POST /products/{productId}/handshake
   ↓
4. Client fetches file with token
   GET /fetch/{fetchToken}
   ↓
5. Extract metadata (unencryptedData/decryptedData)
   ↓
6. Resolve printer from metadata or config
   - Match by serial number
   - Match by nickname
   - Match by IP address
   ↓
7. Upload file to printer
   - FTPS (implicit TLS, port 990)
   - Or Bambu API fallback
   ↓
8. Start print via bambulabs_api
   - Set temperatures
   - Configure AMS
   - Wait for acknowledgment
   ↓
9. Monitor print progress
   - Background status thread
   - Log events at 10% intervals
   ↓
10. Report status to backend
    POST /products/{productId}/status
    ↓
11. On completion, delete remote file
    printer.delete_file()
```

---

### Workflow 2: Command Execution

```
1. CommandWorker polls for commands
   GET /control/pending?recipientId={id}
   ↓
2. Backend returns pending commands
   ↓
3. Worker acknowledges command
   POST /control/ack
   {commandId, status: "processing"}
   ↓
4. Execute command on printer
   - pause → printer.pause()
   - heat → printer.set_bed_temp(), printer.set_nozzle_temp()
   - sendGcode → printer.send_gcode()
   ↓
5. Submit result to backend
   POST /control/result
   {commandId, status: "completed", message: "..."}
```

---

### Workflow 3: Status Streaming

```
┌──────────────────────────────────────────┐
│  BambuStatusSubscriber (background)      │
│                                          │
│  For each configured printer:            │
│  ┌────────────────────────────────────┐  │
│  │ 1. Connect via bambulabs_api       │  │
│  │ 2. Subscribe to MQTT updates       │  │
│  │ 3. Poll get_state(), get_progress()│  │
│  │ 4. Detect errors and conflicts     │  │
│  │ 5. Aggregate metrics               │  │
│  └────────────────────────────────────┘  │
│                ↓                          │
│     POST /api/apps/{app}/functions/      │
│            updatePrinterStatus           │
└──────────────────────────────────────────┘
```

---

## Troubleshooting

### Common Issues

#### 1. Cannot connect to printer

**Symptoms:**
- Timeout errors
- "Connection refused" messages

**Solutions:**
- Verify printer IP address: `ping <printer-ip>`
- Check access code is correct (12 hex characters)
- Ensure printer is on same network or use Bambu Connect
- Try both `transport: "lan"` and `transport: "bambu_connect"`
- Check firewall rules for ports 990 (FTPS) and 8883 (MQTT)

---

#### 2. FTPS upload fails with 550 error

**Symptoms:**
- "550 Permission denied" during upload

**Solutions:**
- Client automatically retries with `SITE ENABLE_STOR`
- Falls back to Bambu API upload
- Set environment variable:
  ```bash
  export BAMBU_FTPS_REACTIVATE_STOR_COMMANDS="ENABLE_STOR"
  ```

---

#### 3. Print doesn't start after upload

**Symptoms:**
- File uploads successfully but print doesn't begin
- No acknowledgment from printer

**Solutions:**
- Enable start debug logging:
  ```bash
  export PRINTMASTER_START_DEBUG=true
  ```
- Check for AMS filament conflicts in logs
- Verify `useAms` setting matches printer configuration
- Increase `waitSeconds` in printer config
- Try setting `useAms: false` for non-AMS prints

---

#### 4. No jobs appear when listening

**Symptoms:**
- Client polls but never finds jobs

**Solutions:**
- Verify recipient ID is correct:
  ```bash
  cat ~/.printmaster/client-info.json
  ```
- Check backend API URL is correct
- Ensure jobs are assigned to your recipient ID in backend
- Test with debug endpoint:
  ```bash
  curl https://printpro3d-api-931368217793.europe-west1.run.app/recipients/{recipientId}/pending
  ```

---

#### 5. Commands not executing

**Symptoms:**
- Commands remain in "pending" state
- No acknowledgment from client

**Solutions:**
- Verify command polling is running
- Check environment variables:
  ```bash
  export CONTROL_POLL_SEC=15
  export CONTROL_POLL_MODE="recipient"
  ```
- Ensure API key is valid
- Check command routing matches (recipientId, printerSerial)
- Review command cache for duplicates:
  ```bash
  cat ~/.printmaster/command-cache.json
  ```

---

#### 6. Camera snapshots not capturing

**Symptoms:**
- No images in `~/.printmaster/camera/`
- Camera errors in logs

**Solutions:**
- Enable camera debug logging:
  ```bash
  export PRINTMASTER_CAMERA_DEBUG=true
  ```
- Verify printer camera is enabled
- Check network connectivity to printer
- Adjust snapshot interval:
  ```bash
  export CAMERA_SNAPSHOT_INTERVAL_SECONDS=60
  ```
- Ensure printer model supports camera (X1C has built-in camera)

---

### Debug Logging

Enable verbose logging for specific components:

```bash
# All debug output
export PRINTMASTER_STATUS_DEBUG=true
export PRINTMASTER_CAMERA_DEBUG=true
export PRINTMASTER_START_DEBUG=true

# Python logging level
export PYTHONLOGLEVEL=DEBUG
```

---

### Log Files

Check these log files for detailed information:

- `~/.printmaster/listener-log.json` - File fetch history
- `~/.printmaster/command-cache.json` - Command execution cache
- Console output - Real-time events and errors

---

## Advanced Configuration

### Multi-Printer Setup

Configure multiple printers in `printers.json`:

```json
[
  {
    "serialNumber": "00M201231234567",
    "nickname": "Printer 1",
    "ipAddress": "192.168.1.100",
    "accessCode": "0123456789AB"
  },
  {
    "serialNumber": "00M201231234568",
    "nickname": "Printer 2",
    "ipAddress": "192.168.1.101",
    "accessCode": "ABCDEF123456"
  }
]
```

Jobs will be automatically routed based on:
1. Serial number match in job metadata
2. Nickname match
3. IP address match
4. First available printer

---

### Offline Testing

Test with local files without backend:

```bash
# Create offline dataset
cat > offline-jobs.json <<EOF
[
  {
    "productId": "test-1",
    "fetchToken": "offline-token",
    "metadata": {
      "unencryptedData": "{\"fileName\":\"test.gcode\"}"
    }
  }
]
EOF

# Run in offline mode
python -m client.client listen \
  --mode offline \
  --offlineDataset offline-jobs.json \
  --recipientId TEST123
```

---

### Custom Transport Strategies

Override transport method per print:

```python
# In job metadata
{
  "transport": "bambu_connect",  # Force cloud relay
  "useCloud": true,
  "cloudTimeout": 300
}
```

---

## Performance Tips

1. **Reduce Poll Interval:** For faster response, set `CONTROL_POLL_SEC=5`
2. **Increase Camera Interval:** For less bandwidth, set `CAMERA_SNAPSHOT_INTERVAL_SECONDS=120`
3. **Disable Calibrations:** Turn off unnecessary calibrations in printer config
4. **Use LAN Transport:** Direct LAN is faster than Bambu Connect
5. **SSD Storage:** Store files on SSD for faster processing

---

## Support

For issues or questions:
- Check log files in `~/.printmaster/`
- Review printer configuration in `printers.json`
- Verify network connectivity with `ping`
- Test backend API with `curl`
- Enable debug logging for detailed output

---

## License

[Specify your license here]
