# Bambu LAN Integration Overview

This project now supports two LAN upload strategies for Bambu Lab printers:

* **`legacy`** – the existing implicit FTPS uploader with manual MQTT control.
* **`bambuApi`** – an optional path that delegates the upload step to the official `bambulabs_api` package while still reusing our MQTT flow.

Set `lanStrategy` to `bambuApi` in the printer configuration to switch to the official client. When the dependency is not available, the code raises a clear error so operators know they must install `bambulabs_api` (for example via `pip install bambulabs_api paho-mqtt rich`).

## Why the official client matters

Bambu’s third-party integration stack couples FTPS uploads with MQTT commands. The official SDK encapsulates a handful of quirks that are difficult to reproduce reliably:

* **Credential handling** – the LAN access code is negotiated automatically and applied to both FTPS and MQTT sessions.
* **Implicit FTPS tuning** – the SDK selects the right cipher suites and connection flags so the printer accepts uploads without returning `550` errors.
* **G-code packaging** – raw `.gcode` files are wrapped on the fly into a minimal `.3mf` container (`Metadata/plate_1.gcode`) before upload, matching the format the printer expects.
* **Status monitoring** – MQTT subscriptions stream progress, temperature, and remaining time updates in real time.

Combining the SDK with our existing status callbacks preserves the same downstream processing that other parts of the system expect.

## Configuration recap

1. Enable *LAN Mode* ("Developer Mode") on the printer to reveal the LAN access code.
2. Collect the printer IP address and serial number from the printer’s settings panel.
3. Install the optional dependencies where the client runs:
   ```bash
   pip install bambulabs_api paho-mqtt rich
   ```
4. Extend the printer entry in the local configuration with:
   ```json
   {
     "serialNumber": "01S00C37134327",
     "ipAddress": "192.168.86.63",
     "accessCode": "22917575",
     "lanStrategy": "bambuApi"
   }
   ```
5. Dispatching a job now leverages the official upload path; MQTT start and progress tracking continue to work as before.

Refer to the following resources for deeper background:

* Bambu Lab wiki – third-party integration guide.
* [`bambulabs_api` project page](https://pypi.org/project/bambulabs-api/) and API reference.
* Community tooling docs at <https://bambutools.github.io/bambulabs_api/>.

