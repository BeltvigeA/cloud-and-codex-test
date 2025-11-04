# Quick Start Guide - Bambu Lab Z-Pulsing Autoprint

Fast reference for getting started with the Bambu Lab autoprint system.

## Prerequisites

- Python 3.8+
- Bambu Lab printer (P1P/P1S/X1/X1C/A1/A1 mini)
- Printer connected to LAN (same network as your computer)

## 3-Step Installation

### 1. Install Dependencies

```bash
cd client
pip install bambulabs-api Pillow
```

### 2. Find Your Printer Info

You need three things:

| Info | Where to Find It |
|------|------------------|
| **IP Address** | Printer screen → Settings → Network → WLAN → IP Address |
| **Serial Number** | Bottom of printer or on box (15 chars, e.g., `01P00A381200434`) |
| **Access Code** | Bambu Studio → Device tab → Your printer → Access Code (8 digits) |

### 3. Test Generation

```bash
python run_autoprint.py \
  --ip YOUR_PRINTER_IP \
  --serial YOUR_SERIAL \
  --access-code YOUR_CODE \
  --generate-only
```

This creates `zpulse.gcode` and `zpulse.3mf` without connecting to printer.

## Basic Usage

### Default Configuration (40 pulses, 5mm distance)

```bash
python run_autoprint.py \
  --ip 192.168.1.100 \
  --serial 01P00A381200434 \
  --access-code 12345678
```

**Output:**
- `autoprint_output/zpulse.gcode` - G-code file
- `autoprint_output/zpulse.3mf` - 3MF package
- `autoprint_output/pulse_001_bottom.jpg` through `pulse_040_top.jpg` - 80 images

### Custom Output Directory

```bash
python run_autoprint.py \
  --ip 192.168.1.100 \
  --serial 01P00A381200434 \
  --access-code 12345678 \
  --output-dir ~/my_bambu_tests
```

### Short Test (5 pulses)

```bash
python run_autoprint.py \
  --ip 192.168.1.100 \
  --serial 01P00A381200434 \
  --access-code 12345678 \
  --num-pulses 5
```

Captures 10 images (2 per pulse) instead of 80.

## Common Options

```bash
# Basic options
--ip IP                    # Printer IP (required)
--serial SERIAL            # Printer serial (required)
--access-code CODE         # 8-digit access code (required)
--output-dir DIR           # Where to save files (default: ./autoprint_output)

# Pulse configuration
--num-pulses N             # Number of pulses (default: 40)
--pulse-distance MM        # Distance to move in mm (default: 5.0)
--dwell-time SECONDS       # Wait time at each position (default: 3.0)
--feed-rate MM/MIN         # Z-axis speed (default: 300.0)

# Execution
--generate-only            # Only create files, don't connect to printer
--no-home                  # Skip homing before starting
--verbose                  # Show detailed debug output
```

## Example Commands

### Testing - Generate Files Only

```bash
# Creates G-code and .3mf without printer
python run_autoprint.py \
  --ip 192.168.1.100 \
  --serial TEST \
  --access-code 12345678 \
  --generate-only \
  --output-dir /tmp/test
```

### Quick Test - 5 Pulses

```bash
# Fast test with real printer (10 images, ~1 minute)
python run_autoprint.py \
  --ip 192.168.1.100 \
  --serial 01P00A381200434 \
  --access-code 12345678 \
  --num-pulses 5 \
  --verbose
```

### Full Job - 40 Pulses

```bash
# Complete job (80 images, ~5 minutes)
python run_autoprint.py \
  --ip 192.168.1.100 \
  --serial 01P00A381200434 \
  --access-code 12345678 \
  --output-dir ~/bambu_captures
```

### Large Movement

```bash
# 10mm pulses, slower movement
python run_autoprint.py \
  --ip 192.168.1.100 \
  --serial 01P00A381200434 \
  --access-code 12345678 \
  --pulse-distance 10.0 \
  --feed-rate 150.0 \
  --num-pulses 20
```

## Expected Output

After running, you'll find in your output directory:

```
autoprint_output/
├── zpulse.gcode          # Raw G-code file
├── zpulse.3mf            # Bambu-compatible package
├── pulse_001_bottom.jpg  # First pulse, bottom position
├── pulse_001_top.jpg     # First pulse, top position
├── pulse_002_bottom.jpg
├── pulse_002_top.jpg
├── ...
└── pulse_040_top.jpg     # Last pulse, top position
```

**Total files:** 2 G-code files + (num_pulses × 2) images

## Troubleshooting

### Can't Connect to Printer

```bash
# 1. Test network connectivity
ping YOUR_PRINTER_IP

# 2. Verify access code in Bambu Studio
# 3. Check printer is not in sleep mode
# 4. Try with verbose logging
python run_autoprint.py --ip ... --serial ... --access-code ... --verbose
```

### Camera Not Working

**X1/X1C users:** Requires firmware ≥ 2.7.0

**Workaround:** Print will still run, but images won't be captured. Check firmware version on printer.

### Import Errors

```bash
# Install dependencies
pip install bambulabs-api Pillow

# Verify installation
python -c "import bambulabs_api; print('OK')"
```

### Permission Errors

```bash
# Use a directory you have write access to
python run_autoprint.py ... --output-dir ~/Documents/bambu_test
```

## What Happens During a Run

1. **Generates G-code** with your pulse configuration
2. **Packages to .3mf** (Bambu-compatible format)
3. **Connects to printer** via bambulabs_api
4. **Uploads file** and starts print
5. **Captures images** at precise moments (2.6s into each 3s dwell)
6. **Monitors progress** until complete
7. **Disconnects** and saves all files

## Next Steps

- Read [AUTOPRINT_README.md](AUTOPRINT_README.md) for complete documentation
- Try different pulse configurations
- Integrate with your own scripts (see Python API examples in README)
- Adjust capture timing if images aren't clear

## Quick Reference Card

| Setting | Default | Range | Purpose |
|---------|---------|-------|---------|
| `num_pulses` | 40 | 1-200 | How many times to pulse |
| `pulse_distance` | 5.0mm | 1.0-20.0mm | How far to move bed |
| `dwell_time` | 3.0s | 1.0-10.0s | Wait time for stability |
| `feed_rate` | 300mm/min | 60-1200mm/min | Movement speed |
| `capture_offset` | 2.6s | 0.5-dwell_time | When to snap image |

**Total Time Estimate:**
- 5 pulses: ~50 seconds
- 10 pulses: ~1.5 minutes
- 20 pulses: ~3 minutes
- 40 pulses: ~5.5 minutes
- 100 pulses: ~14 minutes

**Storage Estimate:**
- Each image: ~100-500 KB
- 40 pulses (80 images): ~20-40 MB
- Plus G-code files: ~10 KB

## Need Help?

1. Run with `--verbose` for detailed output
2. Check [AUTOPRINT_README.md](AUTOPRINT_README.md) for full docs
3. Verify printer info is correct
4. Test with `--generate-only` first
5. Try a short test with `--num-pulses 2`

## Python API Quick Example

```python
from pathlib import Path
from autoprintGcode import run_autoprint_job, ZPulseConfig

# Custom configuration
config = ZPulseConfig(
    num_pulses=20,
    pulse_distance_mm=10.0,
    dwell_time_seconds=5.0
)

# Run job
run_autoprint_job(
    ip="192.168.1.100",
    serial="01P00A381200434",
    access_code="12345678",
    output_dir=Path("~/bambu").expanduser(),
    config=config,
    monitor_progress=True,
    generate_only=False  # Set True to only generate files
)
```

---

**That's it!** You're ready to run autoprint jobs on your Bambu Lab printer.

For detailed documentation, configuration options, and advanced usage, see [AUTOPRINT_README.md](AUTOPRINT_README.md).
