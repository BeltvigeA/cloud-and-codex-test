#!/usr/bin/env python3
"""
CLI wrapper for Bambu Lab Z-Pulsing Autoprint System

This script provides a command-line interface for running autoprint jobs
on Bambu Lab 3D printers using the bambulabs_api library.

Usage examples:
    # Run full autoprint job
    python run_autoprint.py \\
        --ip 192.168.1.100 \\
        --serial 01P00A381200434 \\
        --access-code 12345678 \\
        --output-dir ~/bambu_images

    # Generate files only (no printer connection)
    python run_autoprint.py \\
        --ip 192.168.1.100 \\
        --serial 01P00A381200434 \\
        --access-code 12345678 \\
        --generate-only

    # Custom pulse configuration
    python run_autoprint.py \\
        --ip 192.168.1.100 \\
        --serial 01P00A381200434 \\
        --access-code 12345678 \\
        --num-pulses 20 \\
        --pulse-distance 10.0 \\
        --dwell-time 5.0 \\
        --verbose
"""

import argparse
import logging
import sys
from pathlib import Path

# Add parent directory to path to allow imports
script_dir = Path(__file__).parent
if str(script_dir) not in sys.path:
    sys.path.insert(0, str(script_dir))

try:
    import autoprintGcode
    from autoprintGcode import run_autoprint_job, ZPulseConfig
except ImportError as e:
    print(f"Error: Could not import autoprintGcode module: {e}", file=sys.stderr)
    print("Make sure autoprintGcode.py is in the same directory as this script.", file=sys.stderr)
    sys.exit(1)


def setup_logging(verbose: bool = False) -> None:
    """Configure logging based on verbosity level

    Args:
        verbose: If True, set DEBUG level, otherwise INFO
    """
    log_level = logging.DEBUG if verbose else logging.INFO
    log_format = "%(asctime)s - %(levelname)s - %(message)s"

    if verbose:
        log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    logging.basicConfig(
        level=log_level,
        format=log_format,
        datefmt="%H:%M:%S"
    )


def main() -> int:
    """Main CLI entry point

    Returns:
        Exit code (0 for success, non-zero for failure)
    """
    parser = argparse.ArgumentParser(
        description="Bambu Lab Z-Pulsing Autoprint System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Full autoprint job with default settings
  %(prog)s --ip 192.168.1.100 --serial 01P00A381200434 --access-code 12345678

  # Generate files only (no printer)
  %(prog)s --ip 192.168.1.100 --serial TEST --access-code 12345678 --generate-only

  # Custom configuration
  %(prog)s --ip 192.168.1.100 --serial 01P00A381200434 --access-code 12345678 \\
           --num-pulses 20 --pulse-distance 10.0 --dwell-time 5.0

  # Disable monitoring
  %(prog)s --ip 192.168.1.100 --serial 01P00A381200434 --access-code 12345678 \\
           --no-monitor

For more information, see AUTOPRINT_README.md
        """
    )

    # Required printer connection parameters
    conn_group = parser.add_argument_group("Printer Connection (required)")
    conn_group.add_argument(
        "--ip",
        type=str,
        required=True,
        help="Printer IP address (e.g., 192.168.1.100)"
    )
    conn_group.add_argument(
        "--serial",
        type=str,
        required=True,
        help="Printer serial number (e.g., 01P00A381200434)"
    )
    conn_group.add_argument(
        "--access-code",
        type=str,
        required=True,
        help="Printer access code (8 digits)"
    )

    # Output configuration
    output_group = parser.add_argument_group("Output Configuration")
    output_group.add_argument(
        "--output-dir",
        type=Path,
        default=Path.cwd() / "autoprint_output",
        help="Output directory for G-code, .3mf, and images (default: ./autoprint_output)"
    )

    # Pulse configuration
    pulse_group = parser.add_argument_group("Z-Pulse Configuration")
    pulse_group.add_argument(
        "--pulse-distance",
        type=float,
        default=5.0,
        metavar="MM",
        help="Distance to move bed in mm (default: 5.0)"
    )
    pulse_group.add_argument(
        "--num-pulses",
        type=int,
        default=40,
        metavar="N",
        help="Number of complete pulse cycles (default: 40)"
    )
    pulse_group.add_argument(
        "--dwell-time",
        type=float,
        default=3.0,
        metavar="SECONDS",
        help="Dwell time at each position in seconds (default: 3.0)"
    )
    pulse_group.add_argument(
        "--feed-rate",
        type=float,
        default=300.0,
        metavar="MM/MIN",
        help="Z-axis feed rate in mm/min (default: 300.0)"
    )
    pulse_group.add_argument(
        "--capture-offset",
        type=float,
        default=2.6,
        metavar="SECONDS",
        help="Time offset into dwell for image capture (default: 2.6)"
    )
    pulse_group.add_argument(
        "--no-home",
        action="store_true",
        help="Skip homing before starting pulse sequence"
    )

    # Execution options
    exec_group = parser.add_argument_group("Execution Options")
    exec_group.add_argument(
        "--no-monitor",
        action="store_true",
        help="Disable print progress monitoring"
    )
    exec_group.add_argument(
        "--generate-only",
        action="store_true",
        help="Only generate G-code and .3mf files (don't connect to printer)"
    )

    # Logging
    log_group = parser.add_argument_group("Logging")
    log_group.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose debug logging"
    )

    # Parse arguments
    args = parser.parse_args()

    # Setup logging
    setup_logging(args.verbose)
    log = logging.getLogger(__name__)

    # Display configuration
    log.info("=" * 70)
    log.info("Bambu Lab Z-Pulsing Autoprint System")
    log.info("=" * 70)

    if args.generate_only:
        log.info("Mode: Generate files only (no printer connection)")
    else:
        log.info(f"Printer IP: {args.ip}")
        log.info(f"Serial: {args.serial}")
        log.info(f"Access Code: {'*' * len(args.access_code)}")

    log.info(f"Output Directory: {args.output_dir}")
    log.info("")
    log.info("Pulse Configuration:")
    log.info(f"  • Number of pulses: {args.num_pulses}")
    log.info(f"  • Pulse distance: {args.pulse_distance}mm")
    log.info(f"  • Dwell time: {args.dwell_time}s")
    log.info(f"  • Feed rate: {args.feed_rate}mm/min")
    log.info(f"  • Capture offset: {args.capture_offset}s")
    log.info(f"  • Home before start: {not args.no_home}")
    log.info("")

    # Create configuration object
    config = ZPulseConfig(
        pulse_distance_mm=args.pulse_distance,
        num_pulses=args.num_pulses,
        dwell_time_seconds=args.dwell_time,
        feed_rate_z=args.feed_rate,
        capture_offset_seconds=args.capture_offset,
        home_before_start=not args.no_home
    )

    # Validate configuration
    if config.num_pulses < 1:
        log.error("Error: Number of pulses must be at least 1")
        return 1

    if config.pulse_distance_mm <= 0:
        log.error("Error: Pulse distance must be positive")
        return 1

    if config.dwell_time_seconds <= 0:
        log.error("Error: Dwell time must be positive")
        return 1

    if config.capture_offset_seconds >= config.dwell_time_seconds:
        log.warning(
            f"Warning: Capture offset ({config.capture_offset_seconds}s) is >= "
            f"dwell time ({config.dwell_time_seconds}s). Images may not capture correctly."
        )

    # Calculate expected outputs
    expected_images = config.num_pulses * 2 if not args.generate_only else 0

    if expected_images > 0:
        log.info(f"Expected outputs:")
        log.info(f"  • G-code file: zpulse.gcode")
        log.info(f"  • 3MF package: zpulse.3mf")
        log.info(f"  • Images: {expected_images} (2 per pulse)")
        log.info("")

    # Run the autoprint job
    try:
        run_autoprint_job(
            ip=args.ip,
            serial=args.serial,
            access_code=args.access_code,
            output_dir=args.output_dir,
            config=config,
            monitor_progress=not args.no_monitor,
            generate_only=args.generate_only
        )

        log.info("")
        log.info("=" * 70)
        log.info("✓ Autoprint job completed successfully!")
        log.info("=" * 70)
        log.info(f"Output files saved to: {args.output_dir.absolute()}")

        return 0

    except KeyboardInterrupt:
        log.info("\n\n" + "=" * 70)
        log.warning("✗ Autoprint job interrupted by user")
        log.info("=" * 70)
        return 130  # Standard exit code for SIGINT

    except Exception as e:
        log.error("")
        log.error("=" * 70)
        log.error(f"✗ Autoprint job failed: {e}")
        log.error("=" * 70)

        if args.verbose:
            log.exception("Detailed error traceback:")

        return 1


if __name__ == "__main__":
    sys.exit(main())
