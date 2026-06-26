# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
TEP Data Streaming Service
Sends sensor data from CSV to Ingestion Service (Port 8003)
1 row per second, sequential order
"""
import os
import pandas as pd
import json
import time
import requests
from datetime import datetime, timezone
from pathlib import Path

# Configuration — data files resolve relative to this script, so it runs from any CWD.
BASE_DIR = Path(__file__).resolve().parent
CSV_FILE = BASE_DIR / 'data' / 'tep_streaming_data.csv'
SENSORS_MAPPING = BASE_DIR / 'data' / 'sensors_mapping.json'
EQUIPMENT_ID = os.getenv('EQUIPMENT_ID', '550e8400-e29b-41d4-a716-446655440100')
SITE_ID = os.getenv('SITE_ID', 'factory-tep')

# Device mTLS (ADR-0002): connect to the ingestion edge and present a client certificate
# issued by scripts/device-ca.sh. No bearer token — the tenant comes from the cert SAN.
INGESTION_URL = os.getenv('INGESTION_URL', 'https://localhost:8443/ingest')
DEVICE_CERT = os.getenv('DEVICE_CERT', 'deploy/device-ca/devices/mock-tep/mock-tep.chain.crt')
DEVICE_KEY = os.getenv('DEVICE_KEY', 'deploy/device-ca/devices/mock-tep/mock-tep.key')
CLIENT_CERT = (DEVICE_CERT, DEVICE_KEY)
# Verify the edge's server certificate against this CA bundle; empty = skip (local self-signed).
INGEST_CA = os.getenv('INGEST_CA', '')
SERVER_VERIFY = INGEST_CA if INGEST_CA else False
if not SERVER_VERIFY:
    requests.packages.urllib3.disable_warnings()

# Load sensor mapping
print("=" * 80)
print("TEP Data Streaming Service")
print("=" * 80)

print("\n1️⃣ Loading sensor mapping...")
with open(SENSORS_MAPPING, 'r') as f:
    sensors = json.load(f)

# Create mapping from sensor_name to sensor_id
sensor_map = {s['sensor_name']: s['sensor_id'] for s in sensors}
print(f"   ✓ Loaded {len(sensor_map)} sensor mappings")

# Load CSV data
print("\n2️⃣ Loading CSV data...")
df = pd.read_csv(CSV_FILE)
print(f"   ✓ Loaded {len(df):,} rows")
print(f"   ✓ Columns: {len(df.columns)} ({df.columns[0]}, {df.columns[1]}, ...)")

# Prepare headers (no bearer token — authentication is the client certificate)
headers = {
    'Content-Type': 'application/json'
}

# Statistics
total_sent = 0
total_errors = 0
start_time = time.time()

print("\n3️⃣ Starting data stream (1 row/second)...")
print("   Press Ctrl+C to stop\n")

try:
    for idx, row in df.iterrows():
        row_start = time.time()
        
        # Get current timestamp (actual time)
        current_timestamp = datetime.now(timezone.utc).isoformat()
        
        # Send each sensor reading
        sensors_sent = 0
        sensors_failed = 0
        
        for sensor_name, sensor_id in sensor_map.items():
            if sensor_name not in row:
                continue
                
            value = float(row[sensor_name])
            
            # Build payload
            payload = {
                "timestamp": current_timestamp,
                "sensor_id": sensor_id,
                "equipment_id": EQUIPMENT_ID,
                "site_id": SITE_ID,
                "value": value,
                "unit": "varies",
                "quality_code": 0
            }
            
            # Send to ingestion service
            try:
                response = requests.post(INGESTION_URL, json=payload, headers=headers,
                                         cert=CLIENT_CERT, verify=SERVER_VERIFY, timeout=2)
                if response.status_code == 202:
                    sensors_sent += 1
                else:
                    sensors_failed += 1
                    if sensors_failed == 1:  # Only print first error
                        print(f"   ⚠️  Response {response.status_code}: {response.text[:100]}")
            except Exception as e:
                sensors_failed += 1
                if sensors_failed == 1:  # Only print first error
                    print(f"   ⚠️  Error: {str(e)[:100]}")
        
        # Update statistics
        if sensors_failed == 0:
            total_sent += 1
        else:
            total_errors += 1
        
        # Print progress
        anomaly_label = int(row['is_anomaly']) if 'is_anomaly' in row else -1
        fault_num = int(row['faultNumber']) if 'faultNumber' in row else -1
        
        print(f"   Row {idx+1:5d}/{len(df)} | Sent: {sensors_sent:2d}/52 | "
              f"Anomaly: {anomaly_label} | Fault: {fault_num:2d} | "
              f"Time: {current_timestamp}")
        
        # Wait for next second
        elapsed = time.time() - row_start
        sleep_time = max(0, 1.0 - elapsed)
        if sleep_time > 0:
            time.sleep(sleep_time)
            
except KeyboardInterrupt:
    print("\n\n🛑 Streaming stopped by user")

# Final statistics
elapsed_total = time.time() - start_time
print("\n" + "=" * 80)
print("📊 Streaming Statistics")
print("=" * 80)
print(f"   Total rows processed: {total_sent + total_errors:,}")
print(f"   Successful: {total_sent:,}")
print(f"   Failed: {total_errors:,}")
print(f"   Total time: {elapsed_total:.1f} seconds")
print(f"   Average rate: {(total_sent + total_errors) / elapsed_total:.2f} rows/sec")
print("=" * 80)
