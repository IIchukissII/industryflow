# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Performance Test Script for IndustryFlow
Tests throughput with configurable msg/sec rate
Logs: throughput, latency, errors, system metrics
"""
import pandas as pd
import json
import time
import requests
import psutil
import threading
from datetime import datetime, timezone
from collections import deque
from statistics import mean, median
import csv

# Configuration
CSV_FILE = '../../extensions/tep-reference/producer/data/tep_streaming_data.csv'
SENSORS_MAPPING = '../../extensions/tep-reference/producer/data/sensors_mapping.json'
INGESTION_URL = 'http://localhost:8003/ingest'
EQUIPMENT_ID = '550e8400-e29b-41d4-a716-446655440100'
SITE_ID = 'factory-tep'
JWT_TOKEN = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJiZDZhOGE0NC1lMDE4LTQwNDItODUzOS1jODRjZjY3MTU5MjEiLCJhdWQiOlsiZmFzdGFwaS11c2VyczphdXRoIl0sImV4cCI6MTc2MzY2NDU5OH0.2sZ5gp5MNM77-vXbJ2-52XGacqtsLaO6CLy_mmDLeRY'

# Test parameters (set these before running)
TARGET_MSG_PER_SEC = 500  # messages per second
TEST_DURATION_SEC = 300   # 5 minutes
RESULTS_FILE = 'performance_results_W3_clean.csv'

class PerformanceMonitor:
    def __init__(self):
        self.latencies = deque(maxlen=1000)
        self.sent_count = 0
        self.error_count = 0
        self.start_time = time.time()
        self.lock = threading.Lock()
        
    def record_success(self, latency_ms):
        with self.lock:
            self.latencies.append(latency_ms)
            self.sent_count += 1
            
    def record_error(self):
        with self.lock:
            self.error_count += 1
            
    def get_stats(self):
        with self.lock:
            elapsed = time.time() - self.start_time
            return {
                'sent': self.sent_count,
                'errors': self.error_count,
                'elapsed': elapsed,
                'throughput': self.sent_count / elapsed if elapsed > 0 else 0,
                'latency_avg': mean(self.latencies) if self.latencies else 0,
                'latency_median': median(self.latencies) if self.latencies else 0,
                'latency_p95': sorted(self.latencies)[int(len(self.latencies) * 0.95)] if len(self.latencies) > 20 else 0,
                'latency_max': max(self.latencies) if self.latencies else 0
            }

def get_system_metrics():
    """Get current system metrics"""
    cpu_percent = psutil.cpu_percent(interval=1)
    memory = psutil.virtual_memory()
    return {
        'cpu_percent': cpu_percent,
        'memory_percent': memory.percent,
        'memory_used_gb': memory.used / (1024**3)
    }

def send_measurement(sensor_id, value, unit, timestamp, headers, monitor):
    """Send single measurement and record metrics"""
    payload = {
        'timestamp': timestamp,
        'sensor_id': sensor_id,
        'equipment_id': EQUIPMENT_ID,
        'site_id': SITE_ID,
        'value': float(value),
        'unit': unit,
        'quality_code': 0
    }
    
    start = time.time()
    try:
        response = requests.post(INGESTION_URL, json=payload, headers=headers, timeout=5)
        latency_ms = (time.time() - start) * 1000
        
        if response.status_code == 202:
            monitor.record_success(latency_ms)
            return True
        else:
            monitor.record_error()
            return False
    except Exception:
        monitor.record_error()
        return False

def main():
    print("=" * 80)
    print("IndustryFlow Performance Test")
    print("=" * 80)
    print(f"Target rate: {TARGET_MSG_PER_SEC} msg/sec")
    print(f"Duration: {TEST_DURATION_SEC} seconds")
    print(f"Results: {RESULTS_FILE}")
    print("=" * 80)
    
    # Load sensor mapping
    print("\n📥 Loading data...")
    with open(SENSORS_MAPPING, 'r') as f:
        sensors = json.load(f)
    sensor_map = {s['sensor_name']: s['sensor_id'] for s in sensors}
    
    # Load CSV
    df = pd.read_csv(CSV_FILE)
    print(f"✓ Loaded {len(sensors)} sensors, {len(df):,} rows")
    
    # Prepare headers
    headers = {
        'Authorization': f'Bearer {JWT_TOKEN}',
        'Content-Type': 'application/json'
    }
    
    # Initialize monitor
    monitor = PerformanceMonitor()
    
    # Calculate interval between messages
    interval = 1.0 / TARGET_MSG_PER_SEC
    
    print(f"\n🚀 Starting test... (interval: {interval*1000:.2f}ms)")
    print("Press Ctrl+C to stop\n")
    
    # CSV logging
    csv_file = open(RESULTS_FILE, 'w', newline='')
    csv_writer = csv.writer(csv_file)
    csv_writer.writerow([
        'timestamp', 'elapsed_sec', 'sent_total', 'errors_total',
        'throughput_msg_sec', 'latency_avg_ms', 'latency_median_ms',
        'latency_p95_ms', 'latency_max_ms', 'cpu_percent', 'memory_percent'
    ])
    csv_file.flush()
    
    try:
        test_start = time.time()
        row_idx = 0
        sensor_idx = 0
        
        while (time.time() - test_start) < TEST_DURATION_SEC:
            # Get current row
            row = df.iloc[row_idx % len(df)]
            
            # Get list of sensor columns (skip non-sensor columns)
            sensor_cols = [col for col in df.columns if col.startswith('xmeas_') or col.startswith('xmv_')]
            
            # Get current sensor
            if sensor_idx >= len(sensor_cols):
                sensor_idx = 0
                row_idx += 1
            
            sensor_name = sensor_cols[sensor_idx]
            sensor_id = sensor_map.get(sensor_name)
            
            if sensor_id:
                timestamp = datetime.now(timezone.utc).isoformat()
                value = row[sensor_name]
                unit = 'unit'  # Simplified
                
                send_measurement(sensor_id, value, unit, timestamp, headers, monitor)
            
            sensor_idx += 1
            
            # Log every 10 seconds
            if int(time.time() - test_start) % 10 == 0 and (time.time() - test_start) % 10 < interval:
                stats = monitor.get_stats()
                sys_metrics = get_system_metrics()
                
                csv_writer.writerow([
                    datetime.now().isoformat(),
                    stats['elapsed'],
                    stats['sent'],
                    stats['errors'],
                    stats['throughput'],
                    stats['latency_avg'],
                    stats['latency_median'],
                    stats['latency_p95'],
                    stats['latency_max'],
                    sys_metrics['cpu_percent'],
                    sys_metrics['memory_percent']
                ])
                csv_file.flush()
                
                print(f"[{stats['elapsed']:.0f}s] "
                      f"Sent: {stats['sent']:,} | "
                      f"Throughput: {stats['throughput']:.1f} msg/s | "
                      f"Latency: {stats['latency_avg']:.1f}ms | "
                      f"CPU: {sys_metrics['cpu_percent']:.1f}%")
            
            # Sleep to maintain target rate
            time.sleep(interval)
            
    except KeyboardInterrupt:
        print("\n\n🛑 Test stopped by user")
    
    # Final stats
    final_stats = monitor.get_stats()
    final_sys = get_system_metrics()
    
    csv_writer.writerow([
        datetime.now().isoformat(),
        final_stats['elapsed'],
        final_stats['sent'],
        final_stats['errors'],
        final_stats['throughput'],
        final_stats['latency_avg'],
        final_stats['latency_median'],
        final_stats['latency_p95'],
        final_stats['latency_max'],
        final_sys['cpu_percent'],
        final_sys['memory_percent']
    ])
    csv_file.close()
    
    print("\n" + "=" * 80)
    print("📊 Final Results")
    print("=" * 80)
    print(f"Duration: {final_stats['elapsed']:.1f} seconds")
    print(f"Messages sent: {final_stats['sent']:,}")
    print(f"Errors: {final_stats['errors']:,}")
    print(f"Throughput: {final_stats['throughput']:.1f} msg/sec")
    print(f"Latency avg: {final_stats['latency_avg']:.1f} ms")
    print(f"Latency p95: {final_stats['latency_p95']:.1f} ms")
    print(f"CPU: {final_sys['cpu_percent']:.1f}%")
    print(f"Memory: {final_sys['memory_percent']:.1f}%")
    print("=" * 80)
    print(f"\n✓ Results saved to: {RESULTS_FILE}")

if __name__ == '__main__':
    main()
