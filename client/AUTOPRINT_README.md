# Bambu Lab Z-Pulsing Autoprint System

Complete autoprint system for Bambu Lab 3D printers that generates G-code for Z-axis pulsing, uploads via LAN API, and captures images during the print process.

**CRITICAL:** This system uses **ONLY** the `bambulabs_api` Python library for all printer communication. No other printer communication methods are used.

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Usage](#usage)
  - [CLI Usage](#cli-usage)
  - [Python API Usage](#python-api-usage)
- [How to Find Printer Information](#how-to-find-printer-information)
- [G-Code Structure](#g-code-structure)
- [Image Capture Timing](#image-capture-timing)
- [Bambu Labs API Details](#bambu-labs-api-details)
- [Configuration Options](#configuration-options)
- [Troubleshooting](#troubleshooting)
- [Advanced Usage](#advanced-usage)

## Overview

This autoprint system enables automated Z-axis pulsing on Bambu Lab printers with synchronized image capture. It's designed for applications requiring precise bed movement and camera monitoring, such as:

- Bed surface inspection
- Layer adhesion testing
- Camera calibration
- Time-lapse photography with controlled movement
- Quality control testing

### What It Does

1. **Generates G-code** for Z-axis pulsing with configurable parameters
2. **Packages G-code** into `.3mf` format (Bambu-compatible)
3. **Uploads and starts** the print job via the Bambu Labs API
4. **Captures images** at precise moments during the print (2 per pulse)
5. **Monitors progress** and provides real-time status updates

### Architecture

```
┌─────────────────────────────────────────────────────┐
│  run_autoprint.py (CLI)                             │
│  ↓                                                   │
│  autoprintGcode.py (Core Module)                    │
│  ├── Generate G-code                                │
│  ├── Package to .3mf                                │
│  ├── BambuAutoPrinter class                         │
│  │   └── bambulabs_api.Printer                      │
│  │       ├── MQTT (port 8883)                       │
│  │       ├── FTPS (port 990)                        │
│  │       └── Camera (port 6000)                     │
│  └── Image capture & monitoring                     │
└─────────────────────────────────────────────────────┘
```

## Features

✅ **Pure bambulabs_api implementation** - No manual MQTT/FTPS/HTTP handling
✅ **Complete G-code generation** - Customizable pulse parameters
✅ **Automatic .3mf packaging** - Bambu-compatible format
✅ **Precise image capture** - Synchronized with print movements
✅ **Real-time monitoring** - Track print progress and status
✅ **Generate-only mode** - Test without printer connection
✅ **Full type hints** - IDE-friendly with complete type annotations
✅ **Comprehensive error handling** - Graceful failure recovery
✅ **Detailed logging** - Debug and INFO levels available

## Requirements

### Hardware

- **Bambu Lab Printer** (P1P, P1S, X1, X1C, A1, A1 mini)
- **LAN Connection** (printer connected to same network as computer)
- **Camera Support** (for image capture):
  - P1P/P1S: Full support
  - A1/A1 mini: Full support
  - X1/X1C: Requires firmware ≥ 2.7.0

### Software

- Python 3.8 or higher
- `bambulabs_api` library (≥ 1.0.0)
- `Pillow` library (≥ 10.0)

## Installation

### Step 1: Install Dependencies

```bash
cd client
pip install bambulabs-api Pillow
```

Or if you have a `requirements.txt`:

```bash
pip install -r requirements.txt
```

### Step 2: Verify Installation

```bash
python -c "import bambulabs_api; print('bambulabs_api version:', bambulabs_api.__version__)"
```

### Step 3: Test G-code Generation

```bash
python run_autoprint.py \
  --ip 192.168.1.100 \
  --serial TEST12345678 \
  --access-code 12345678 \
  --generate-only
```

This will create `zpulse.gcode` and `zpulse.3mf` without connecting to a printer.

## Quick Start

See [QUICK_START.md](QUICK_START.md) for a condensed reference guide.

### Basic Usage

```bash
python run_autoprint.py \
  --ip 192.168.1.100 \
  --serial 01P00A381200434 \
  --access-code 12345678 \
  --output-dir ~/bambu_autoprint
```

This will:
- Generate G-code for 40 pulses (default)
- Connect to the printer
- Upload and start the print
- Capture 80 images (2 per pulse)
- Save everything to `~/bambu_autoprint/`

## Usage

### CLI Usage

#### Full Autoprint Job

```bash
python run_autoprint.py \
  --ip 192.168.1.100 \
  --serial 01P00A381200434 \
  --access-code 12345678 \
  --output-dir ~/bambu_images \
  --num-pulses 40 \
  --verbose
```

#### Generate Files Only (No Printer)

```bash
python run_autoprint.py \
  --ip 192.168.1.100 \
  --serial TEST \
  --access-code 12345678 \
  --generate-only \
  --output-dir /tmp/test_output
```

#### Custom Pulse Configuration

```bash
python run_autoprint.py \
  --ip 192.168.1.100 \
  --serial 01P00A381200434 \
  --access-code 12345678 \
  --num-pulses 20 \
  --pulse-distance 10.0 \
  --dwell-time 5.0 \
  --feed-rate 600.0
```

#### Available Options

```
Printer Connection (required):
  --ip IP                  Printer IP address
  --serial SERIAL          Printer serial number
  --access-code CODE       Printer access code (8 digits)

Output Configuration:
  --output-dir DIR         Output directory (default: ./autoprint_output)

Z-Pulse Configuration:
  --pulse-distance MM      Distance to move bed in mm (default: 5.0)
  --num-pulses N           Number of pulse cycles (default: 40)
  --dwell-time SECONDS     Dwell time at each position (default: 3.0)
  --feed-rate MM/MIN       Z-axis feed rate (default: 300.0)
  --capture-offset SECONDS Image capture offset into dwell (default: 2.6)
  --no-home                Skip homing before starting

Execution Options:
  --no-monitor             Disable print progress monitoring
  --generate-only          Only generate files, don't connect to printer

Logging:
  --verbose, -v            Enable verbose debug logging
```

### Python API Usage

#### Basic Example

```python
from pathlib import Path
from autoprintGcode import run_autoprint_job, ZPulseConfig

# Use default configuration (40 pulses, 5mm distance)
run_autoprint_job(
    ip="192.168.1.100",
    serial="01P00A381200434",
    access_code="12345678",
    output_dir=Path("~/bambu_images").expanduser(),
    config=None,  # Uses defaults
    monitor_progress=True,
    generate_only=False
)
```

#### Custom Configuration

```python
from pathlib import Path
from autoprintGcode import run_autoprint_job, ZPulseConfig

# Create custom configuration
config = ZPulseConfig(
    pulse_distance_mm=10.0,
    num_pulses=20,
    dwell_time_seconds=5.0,
    feed_rate_z=600.0,
    capture_offset_seconds=3.0,
    home_before_start=True
)

run_autoprint_job(
    ip="192.168.1.100",
    serial="01P00A381200434",
    access_code="12345678",
    output_dir=Path("~/bambu_images").expanduser(),
    config=config,
    monitor_progress=True,
    generate_only=False
)
```

#### Generate Files Only

```python
from pathlib import Path
from autoprintGcode import generate_zpulse_gcode, package_gcode_to_3mf, ZPulseConfig

# Generate G-code
config = ZPulseConfig(num_pulses=10)
gcode = generate_zpulse_gcode(config)

# Save G-code
Path("output.gcode").write_text(gcode)

# Package to .3mf
threemf_bytes = package_gcode_to_3mf(gcode, plate_number=1)
Path("output.3mf").write_bytes(threemf_bytes)
```

#### Direct Printer Control

```python
from pathlib import Path
from autoprintGcode import BambuAutoPrinter

# Initialize printer
printer = BambuAutoPrinter(
    ip="192.168.1.100",
    serial="01P00A381200434",
    access_code="12345678",
    connect_camera=True
)

# Connect
printer.connect()

try:
    # Upload and start print
    threemf_bytes = Path("zpulse.3mf").read_bytes()
    success = printer.upload_and_start_print(
        threemf_bytes=threemf_bytes,
        filename="zpulse.3mf",
        plate_number=1,
        use_ams=False
    )

    if success:
        # Capture image
        printer.capture_image(Path("test_image.jpg"))

        # Get status
        status = printer.get_print_status()
        print(f"State: {status['state']}")
        print(f"Progress: {status['percentage']}%")

finally:
    # Always disconnect
    printer.disconnect()
```

## How to Find Printer Information

You need three pieces of information to connect to your Bambu Lab printer:

### 1. IP Address

**Method 1: From Printer Screen**
1. Tap the settings icon on printer screen
2. Go to Network → WLAN
3. Look for "IP Address"

**Method 2: From Router**
1. Log into your router admin panel
2. Check connected devices list
3. Look for device named "Bambu-[model]-[serial]"

**Method 3: Network Scan**
```bash
# Linux/Mac
nmap -sn 192.168.1.0/24 | grep -i bambu

# Or use Bambu Handy app to find IP
```

### 2. Serial Number

**Location:** Bottom of the printer or on the box

**Format:** 15 characters, usually starts with `01P` or `01S`

**Example:** `01P00A381200434`

### 3. Access Code

**Method 1: From Bambu Studio**
1. Open Bambu Studio
2. Go to Device tab
3. Click on your printer
4. Look for "Access Code" in printer settings
5. If not set, you can set a new one (8 digits)

**Method 2: From Bambu Handy App**
1. Open Bambu Handy app
2. Select your printer
3. Go to Settings → General → Access Code
4. Set or view the access code (8 digits)

**Note:** The access code must be enabled on the printer. If you can't find it, you need to set it first through Bambu Studio or the Handy app.

## G-Code Structure

The generated G-code follows this structure:

```gcode
; Bambu Lab Z-Pulse G-code
; Generated by autoprintGcode.py
; Pulses: 40
; Distance: 5.0mm
; Dwell: 3.0s
; Feed rate: 300.0mm/min

; Home all axes
G28

; Switch to relative positioning
G91

; Set Z feedrate to 300.0mm/min
G1 F300.0

; Pulse 1/40
G4 S3.0           ; Dwell at bottom (IMAGE CAPTURE)
G0 Z5.0           ; Move bed DOWN (Z up)
M400              ; Wait for moves to complete
G4 S3.0           ; Dwell at top (IMAGE CAPTURE)
G0 Z-5.0          ; Move bed UP (Z down)
M400              ; Wait for moves to complete

; Pulse 2/40
; ... (repeats for each pulse)

; Return to absolute positioning
G90

; Final flush
M400

; Z-Pulse sequence complete
```

### Key G-Code Commands

| Command | Description |
|---------|-------------|
| `G28` | Home all axes |
| `G90` | Absolute positioning mode |
| `G91` | Relative positioning mode |
| `G0 Zn` | Move Z axis by n mm (fast) |
| `G1 Fn` | Set feedrate to n mm/min |
| `G4 Sn` | Dwell (wait) for n seconds |
| `M400` | Wait for all moves to complete |

### Movement Explanation

In **relative mode** (`G91`):
- `G0 Z5.0` → Move **bed DOWN** (nozzle goes UP relatively) by 5mm
- `G0 Z-5.0` → Move **bed UP** (nozzle goes DOWN relatively) by 5mm

This creates the pulsing motion where the bed moves down and up repeatedly.

## Image Capture Timing

Images are captured at precise moments during each dwell period.

### Timing Calculation

For default configuration (40 pulses, 5mm distance, 3s dwell, 300mm/min feedrate):

```
Homing time:          ~10.0 seconds
Movement time:        5mm ÷ (300mm/min ÷ 60) = 1.0 second
Dwell time:           3.0 seconds
Capture offset:       2.6 seconds into dwell

Total time per pulse: (dwell + move + dwell + move) = 3.0 + 1.0 + 3.0 + 1.0 = 8.0 seconds
Total job time:       10.0 + (40 × 8.0) = 330.0 seconds (~5.5 minutes)
Total images:         40 pulses × 2 positions = 80 images
```

### Capture Points

For each pulse:
1. **Bottom position**: Captured at 2.6s into first dwell (before moving up)
2. **Top position**: Captured at 2.6s into second dwell (after moving up)

Example timeline for Pulse 1:
```
Time    Event
-----   -----
10.0s   Start pulse 1
10.0s   Dwell at bottom begins
12.6s   ← CAPTURE: pulse_001_bottom.jpg
13.0s   Dwell ends
13.0s   Move bed down (Z+5mm)
14.0s   Move complete
14.0s   Dwell at top begins
16.6s   ← CAPTURE: pulse_001_top.jpg
17.0s   Dwell ends
17.0s   Move bed up (Z-5mm)
18.0s   Move complete, pulse 1 done
```

### Output Files

Images are saved with naming convention:
```
pulse_001_bottom.jpg
pulse_001_top.jpg
pulse_002_bottom.jpg
pulse_002_top.jpg
...
pulse_040_bottom.jpg
pulse_040_top.jpg
```

## Bambu Labs API Details

This system uses the `bambulabs_api` Python library exclusively.

### API Reference

- **Documentation**: https://bambutools.github.io/bambulabs_api/
- **Examples**: https://bambutools.github.io/bambulabs_api/examples.html
- **API Reference**: https://bambutools.github.io/bambulabs_api/api/printer.html
- **GitHub**: https://github.com/BambuTools/bambulabs_api

### Key Classes and Methods

#### Printer Class

```python
import bambulabs_api as bl

# Initialize
printer = bl.Printer(ip, access_code, serial)

# Connect (with camera)
printer.connect()

# Connect (without camera)
printer.mqtt_start()

# Upload and start print
printer.start_print(
    filename="/path/to/file.3mf",
    plate_number=1,
    use_ams=False,
    flow_calibration=False
)

# Camera capture
image = printer.get_camera_image()  # Returns PIL.Image
image.save("capture.jpg")

# Status queries
state = printer.get_state()                    # e.g., "RUNNING"
percentage = printer.get_percentage()          # 0-100
bed_temp = printer.get_bed_temperature()       # °C
nozzle_temp = printer.get_nozzle_temperature() # °C

# Disconnect
printer.disconnect()  # if using connect()
printer.mqtt_stop()   # if using mqtt_start()
```

### Network Ports

The bambulabs_api library uses these ports:

| Port | Protocol | Purpose |
|------|----------|---------|
| 8883 | MQTT/TLS | Command & status communication |
| 990 | FTPS | File upload (implicit TLS) |
| 6000 | JPEG/TCP | Camera stream |

**Firewall Note:** Ensure these ports are not blocked on your network.

### Camera Support

| Printer Model | Camera Support | Notes |
|---------------|----------------|-------|
| P1P | ✅ Full | Native support |
| P1S | ✅ Full | Native support |
| A1 | ✅ Full | Native support |
| A1 mini | ✅ Full | Native support |
| X1 | ⚠️ Limited | Requires firmware ≥ 2.7.0 |
| X1C | ⚠️ Limited | Requires firmware ≥ 2.7.0 |

If camera is not available, the system will continue but skip image captures.

## Configuration Options

### ZPulseConfig Dataclass

```python
@dataclass
class ZPulseConfig:
    pulse_distance_mm: float = 5.0      # Distance to move bed (mm)
    num_pulses: int = 40                # Number of complete pulse cycles
    dwell_time_seconds: float = 3.0     # Dwell time at each position (s)
    feed_rate_z: float = 300.0          # Z-axis feed rate (mm/min)
    capture_offset_seconds: float = 2.6 # Capture offset into dwell (s)
    home_before_start: bool = True      # Home printer before starting
```

### Parameter Guidelines

| Parameter | Min | Max | Recommended | Notes |
|-----------|-----|-----|-------------|-------|
| `pulse_distance_mm` | 1.0 | 20.0 | 5.0 | Don't exceed printer Z range |
| `num_pulses` | 1 | 200 | 40 | More pulses = longer print time |
| `dwell_time_seconds` | 1.0 | 10.0 | 3.0 | Allow stabilization time |
| `feed_rate_z` | 60.0 | 1200.0 | 300.0 | Slower = smoother movement |
| `capture_offset_seconds` | 0.5 | dwell_time-0.1 | 2.6 | Time to capture image in dwell |

### Example Configurations

#### Fast Testing (5 pulses)
```python
ZPulseConfig(
    pulse_distance_mm=5.0,
    num_pulses=5,
    dwell_time_seconds=2.0,
    feed_rate_z=600.0
)
```

#### Precise Capture (slower, more stable)
```python
ZPulseConfig(
    pulse_distance_mm=5.0,
    num_pulses=40,
    dwell_time_seconds=5.0,
    feed_rate_z=150.0,
    capture_offset_seconds=4.0
)
```

#### Large Movement
```python
ZPulseConfig(
    pulse_distance_mm=10.0,
    num_pulses=20,
    dwell_time_seconds=4.0,
    feed_rate_z=300.0
)
```

## Troubleshooting

### Connection Issues

#### Problem: "Failed to connect to printer"

**Possible causes:**
1. Wrong IP address
2. Printer not on same network
3. Access code incorrect
4. Firewall blocking ports

**Solutions:**
```bash
# Test network connectivity
ping 192.168.1.100

# Verify IP address from printer screen
# Check access code in Bambu Studio

# Test with verbose logging
python run_autoprint.py --ip ... --serial ... --access-code ... --verbose
```

#### Problem: "Connection timeout"

**Possible causes:**
1. Printer is offline
2. Firewall blocking ports 8883, 990, or 6000
3. Network congestion

**Solutions:**
```bash
# Check if ports are accessible
telnet 192.168.1.100 8883
telnet 192.168.1.100 990

# Temporarily disable firewall for testing
# Check printer is not in sleep mode
```

### Camera Issues

#### Problem: "Failed to capture image"

**Possible causes:**
1. Camera not supported on printer model
2. Firmware too old (X1/X1C)
3. Camera not enabled in settings

**Solutions:**
```bash
# Check firmware version (must be ≥ 2.7.0 for X1/X1C)
# Enable camera in printer settings
# Run without camera:
python run_autoprint.py ... --no-monitor  # Still uploads but skips images
```

#### Problem: Images are black or corrupted

**Possible causes:**
1. Capturing too early (before camera stabilizes)
2. Network bandwidth issues
3. Camera hardware issue

**Solutions:**
```python
# Increase dwell time and capture offset
config = ZPulseConfig(
    dwell_time_seconds=5.0,
    capture_offset_seconds=4.0
)
```

### G-Code Issues

#### Problem: Printer doesn't move as expected

**Possible causes:**
1. Printer still in absolute mode from previous job
2. Z-axis not homed properly
3. Feedrate too high/low

**Solutions:**
```bash
# Ensure homing is enabled
python run_autoprint.py ... --no-home  # If you want to skip homing

# Adjust feedrate
python run_autoprint.py ... --feed-rate 150.0  # Slower movement
```

#### Problem: "Print failed to start"

**Possible causes:**
1. Printer already printing
2. Plate not ready
3. Invalid .3mf file

**Solutions:**
```bash
# Check printer status first
# Verify .3mf is valid (test with --generate-only)
python run_autoprint.py ... --generate-only

# Then manually upload .3mf to verify it's valid
```

### File Issues

#### Problem: "Permission denied" when saving files

**Possible causes:**
1. Output directory doesn't exist
2. No write permissions
3. Disk full

**Solutions:**
```bash
# Use a directory you have permissions for
python run_autoprint.py ... --output-dir ~/Documents/bambu_test

# Check disk space
df -h
```

### Import Errors

#### Problem: "ModuleNotFoundError: No module named 'bambulabs_api'"

**Solution:**
```bash
pip install bambulabs-api

# Or with specific version
pip install bambulabs-api>=2.6.5
```

#### Problem: "No module named 'autoprintGcode'"

**Solution:**
```bash
# Make sure you're running from the client/ directory
cd client
python run_autoprint.py ...
```

## Advanced Usage

### Custom Image Processing

```python
from pathlib import Path
from autoprintGcode import BambuAutoPrinter
from PIL import Image, ImageEnhance

printer = BambuAutoPrinter("192.168.1.100", "01P00A381200434", "12345678")
printer.connect()

try:
    # Capture image
    img_path = Path("raw_capture.jpg")
    if printer.capture_image(img_path):
        # Load and enhance
        img = Image.open(img_path)

        # Adjust brightness
        enhancer = ImageEnhance.Brightness(img)
        img = enhancer.enhance(1.2)

        # Adjust contrast
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(1.5)

        # Save enhanced
        img.save("enhanced_capture.jpg", quality=95)
finally:
    printer.disconnect()
```

### Progress Callback

```python
from pathlib import Path
from autoprintGcode import BambuAutoPrinter, ZPulseConfig, calculate_capture_points
import time

def progress_callback(current: int, total: int, status: dict):
    """Called periodically with progress updates"""
    print(f"Progress: {current}/{total} images - {status['percentage']}%")

printer = BambuAutoPrinter("192.168.1.100", "01P00A381200434", "12345678")
config = ZPulseConfig(num_pulses=10)
capture_points = calculate_capture_points(config)

printer.connect()
try:
    # Start print...
    # (upload code here)

    # Monitor with callback
    start_time = time.time()
    for i, point in enumerate(capture_points):
        while time.time() - start_time < point.timestamp_seconds:
            time.sleep(0.1)

        img_path = Path(f"captures/{point.filename}")
        if printer.capture_image(img_path):
            status = printer.get_print_status()
            progress_callback(i + 1, len(capture_points), status)
finally:
    printer.disconnect()
```

### Batch Processing Multiple Printers

```python
from pathlib import Path
from autoprintGcode import BambuAutoPrinter, generate_zpulse_gcode, package_gcode_to_3mf, ZPulseConfig
import concurrent.futures

def run_on_printer(printer_info: dict, gcode_bytes: bytes):
    """Run autoprint on a single printer"""
    printer = BambuAutoPrinter(
        printer_info["ip"],
        printer_info["serial"],
        printer_info["access_code"]
    )

    printer.connect()
    try:
        success = printer.upload_and_start_print(
            threemf_bytes=gcode_bytes,
            filename="zpulse.3mf",
            plate_number=1,
            use_ams=False
        )
        return printer_info["serial"], success
    finally:
        printer.disconnect()

# Generate G-code once
config = ZPulseConfig(num_pulses=20)
gcode = generate_zpulse_gcode(config)
threemf_bytes = package_gcode_to_3mf(gcode)

# List of printers
printers = [
    {"ip": "192.168.1.100", "serial": "01P00A381200434", "access_code": "12345678"},
    {"ip": "192.168.1.101", "serial": "01P00A381200435", "access_code": "12345678"},
    {"ip": "192.168.1.102", "serial": "01P00A381200436", "access_code": "12345678"},
]

# Run in parallel
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
    futures = [executor.submit(run_on_printer, p, threemf_bytes) for p in printers]

    for future in concurrent.futures.as_completed(futures):
        serial, success = future.result()
        print(f"Printer {serial}: {'✓' if success else '✗'}")
```

### Integration with Existing Systems

If you already have a printer management system, you can integrate the autoprint module:

```python
from autoprintGcode import generate_zpulse_gcode, package_gcode_to_3mf, ZPulseConfig
from my_printer_system import MyBambuPrinter  # Your existing printer class

# Generate the G-code
config = ZPulseConfig(num_pulses=30)
gcode = generate_zpulse_gcode(config)
threemf_bytes = package_gcode_to_3mf(gcode)

# Use your existing printer interface
my_printer = MyBambuPrinter(ip="192.168.1.100")
my_printer.upload_file(threemf_bytes, "zpulse.3mf")
my_printer.start_print()
```

## Support & Contributing

### Getting Help

1. Check this README and [QUICK_START.md](QUICK_START.md)
2. Enable verbose logging: `--verbose`
3. Check [bambulabs_api documentation](https://bambutools.github.io/bambulabs_api/)
4. Review Bambu Lab official documentation

### Reporting Issues

When reporting issues, include:
- Python version (`python --version`)
- Library versions (`pip list | grep -i bambu`)
- Complete error message with `--verbose` enabled
- Printer model and firmware version
- Full command used

### Known Limitations

1. **Camera support**: Limited on X1/X1C with firmware < 2.7.0
2. **Network required**: Must be on same LAN as printer
3. **Single print only**: Doesn't support multi-plate or AMS integration
4. **No cloud support**: LAN mode only (by design)
5. **Image timing**: Approximate, may vary ±0.5s depending on printer response

## License

This autoprint system is provided as-is for use with Bambu Lab 3D printers.

## Acknowledgments

- Built with [bambulabs_api](https://github.com/BambuTools/bambulabs_api) library
- Designed for Bambu Lab printer ecosystem
- Part of the PrintMaster client system
