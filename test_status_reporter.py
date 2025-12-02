#!/usr/bin/env python3
"""Simple test script for StatusReporter"""

import logging
from client.status_reporter import StatusReporter

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Test configuration
BASE_URL = "https://printpro3d-api-931368217793.europe-west1.run.app"
API_KEY = "test-key"
RECIPIENT_ID = "test-recipient"

# Test 1: Initialize StatusReporter
print("\n" + "=" * 80)
print("TEST 1: Initialize StatusReporter")
print("=" * 80)

try:
    reporter = StatusReporter(
        base_url=BASE_URL,
        api_key=API_KEY,
        recipient_id=RECIPIENT_ID,
        report_interval=10,
    )
    print("✅ StatusReporter initialized successfully")
except Exception as e:
    print(f"❌ Failed to initialize StatusReporter: {e}")
    exit(1)

# Test 2: Ping printer (will fail, but should handle gracefully)
print("\n" + "=" * 80)
print("TEST 2: Ping printer (localhost - should fail gracefully)")
print("=" * 80)

try:
    result = reporter.ping_printer("127.0.0.1")
    print(f"Ping result: {result}")
    print("✅ Ping test completed")
except Exception as e:
    print(f"❌ Ping test failed: {e}")

# Test 3: Parse MQTT data
print("\n" + "=" * 80)
print("TEST 3: Parse MQTT status data")
print("=" * 80)

test_status_data = {
    "state": "RUNNING",
    "gcodeState": "RUNNING",
    "progressPercent": 45.5,
    "bedTemp": 60.0,
    "nozzleTemp": 220.0,
    "fanSpeedPercent": 80,
    "printSpeed": 100,
    "remainingTimeSeconds": 3600,
    "rawStatePayload": {
        "gcode_file": "test_model.3mf",
        "layer_num": 256,
        "total_layer_num": 512,
        "bed_target_temper": 60,
        "nozzle_target_temper": 220,
        "chamber_temper": 45,
    }
}

try:
    parsed = reporter.parse_print_job_data(test_status_data)
    print("Parsed status:")
    for key, value in parsed.items():
        print(f"  {key}: {value}")
    print("✅ Parse test completed")
except Exception as e:
    print(f"❌ Parse test failed: {e}")

# Test 4: Should report (rate limiting)
print("\n" + "=" * 80)
print("TEST 4: Should report (rate limiting)")
print("=" * 80)

try:
    serial = "01P00A381200434"

    # First call should return True
    should_report_1 = reporter.should_report(serial)
    print(f"First call: should_report = {should_report_1} (expected: True)")

    # Second call immediately should return False
    should_report_2 = reporter.should_report(serial)
    print(f"Second call (immediate): should_report = {should_report_2} (expected: False)")

    if should_report_1 and not should_report_2:
        print("✅ Rate limiting works correctly")
    else:
        print("⚠️  Rate limiting behavior unexpected")

except Exception as e:
    print(f"❌ Rate limiting test failed: {e}")

print("\n" + "=" * 80)
print("ALL TESTS COMPLETED")
print("=" * 80)
print()
print("Note: Actual API calls to backend will fail without valid credentials.")
print("To test with real backend:")
print("  export BASE_URL='https://printpro3d-api-...'")
print("  export API_KEY='your-api-key'")
print("  export RECIPIENT_ID='your-recipient-id'")
print()
