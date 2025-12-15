
import json
import os
import sys
import time
from pathlib import Path

# Import bambuPrinter
try:
    from . import bambuPrinter
except ImportError:
    try:
        import client.bambuPrinter as bambuPrinter
    except ImportError:
        import bambuPrinter


def load_printers():
    path = Path("C:/Users/andre/.printmaster/printers.json")
    if not path.exists():
        print(f"ERROR: {path} does not exist")
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def diagnose():
    with open("diagnostic_output.txt", "w", encoding="utf-8") as log_file:
        def log(msg):
            print(msg)
            log_file.write(str(msg) + "\n")
            
        log("--- STARTING PRINTER DIAGNOSIS ---")
        
        if bambuPrinter.bambulabsApi is None:
            log("ERROR: bambulabs_api not available via bambuPrinter module")
            return

        printers = load_printers()
        if not printers:
            log("No printers found in config.")
            return

        for printer_conf in printers:
            ip = printer_conf.get("ipAddress")
            access_code = printer_conf.get("accessCode")
            serial = printer_conf.get("serialNumber")
            
            log(f"\nScanning Printer: {serial} ({ip})")
            
            try:
                # Use the Printer class from the imported module
                PrinterClass = getattr(bambuPrinter.bambulabsApi, "Printer")
                printer = PrinterClass(ip, access_code, serial)
                
                log("  Connecting (MQTT)...")
                printer.mqtt_start()
                
                # Wait for data
                log("  Waiting for data (15s)...")
                time.sleep(15)
                
                log("  Checking state...")
                try:
                    state = printer.get_state()
                    log(f"  RAW get_state(): {json.dumps(state, default=str)}")
                except Exception as e:
                    log(f"  get_state() FAILED: {e}")
                    
                try:
                    # Check if method exists first
                    gcodeGetter = getattr(printer, "get_gcode_state", None)
                    if callable(gcodeGetter):
                        gcode_state = gcodeGetter()
                        log(f"  RAW get_gcode_state(): {gcode_state}")
                    else:
                        log("  get_gcode_state() method NOT FOUND on printer object")
                except Exception as e:
                    log(f"  get_gcode_state() FAILED: {e}")
                    
                try:
                    dump = printer.mqtt_dump()
                    # Safe dump of the interesting parts
                    if dump:
                         print_section = dump.get('print', {})
                         if isinstance(print_section, dict):
                             log(f"  mqtt_dump['print']['gcode_state']: {print_section.get('gcode_state')}")
                             log(f"  mqtt_dump['print']['print_error']: {print_section.get('print_error')}")
                             log(f"  mqtt_dump['print']['mc_print_error_code']: {print_section.get('mc_print_error_code')}")
                         log(f"  RAW mqtt_dump keys: {list(dump.keys())}")
                    else:
                        log("  mqtt_dump() returned empty/None")
                except Exception as e:
                    log(f"  mqtt_dump() FAILED: {e}")
                    
                try:
                    printer.disconnect()
                except:
                    pass
                
            except Exception as e:
                log(f"  CONNECTION FAILED: {e}")

        log("\n--- DIAGNOSIS COMPLETE ---")


if __name__ == "__main__":
    diagnose()
