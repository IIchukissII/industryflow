# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Real-time metrics collector for bottleneck analysis
Runs alongside performance test and logs metrics every 10 seconds
"""
import subprocess
import time
import csv
from datetime import datetime

METRICS_FILE = 'bottleneck_metrics.csv'
COLLECTION_INTERVAL = 10  # seconds

def run_cmd(cmd):
    """Run shell command and return output"""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)
        return result.stdout.strip()
    except:
        return ""

def get_kafka_total_messages():
    """Get total messages in Kafka topic"""
    cmd = "sudo docker exec industryflow-kafka kafka-run-class kafka.tools.GetOffsetShell --broker-list localhost:9092 --topic sensor-data-raw --time -1 2>/dev/null"
    output = run_cmd(cmd)
    total = 0
    for line in output.split('\n'):
        if ':' in line:
            parts = line.split(':')
            if len(parts) >= 3:
                try:
                    total += int(parts[2])
                except:
                    pass
    return total

def get_spark_metrics():
    """Get Spark processing metrics from logs"""
    cmd = "sudo docker logs industryflow-spark-streaming 2>&1 | grep -oP '\"processedRowsPerSecond\" : \\K[0-9.]+' | tail -1"
    output = run_cmd(cmd)
    try:
        return float(output) if output else 0.0
    except:
        return 0.0

def get_kafka_consumer_lag():
    """Get total consumer lag across all partitions"""
    cmd = "sudo docker exec industryflow-kafka kafka-consumer-groups --bootstrap-server localhost:9092 --describe --all-groups 2>/dev/null | grep sensor-data-raw"
    output = run_cmd(cmd)
    total_lag = 0
    for line in output.split('\n'):
        parts = line.split()
        if len(parts) >= 6:
            try:
                lag = int(parts[5])
                total_lag += lag
            except:
                pass
    return total_lag

def get_db_write_rate():
    """Get database insert rate (rows inserted in last interval)"""
    cmd = """sudo docker exec industryflow-timescaledb psql -U postgres -d industryflow -t -c "
    SELECT COALESCE(SUM(n_tup_ins), 0) 
    FROM pg_stat_user_tables 
    WHERE schemaname LIKE 'tenant_%' 
    AND tablename = 'sensor_measurements';"
    """
    output = run_cmd(cmd)
    try:
        return int(output.strip()) if output.strip() else 0
    except:
        return 0

def get_container_cpu(container):
    """Get CPU% for container"""
    cmd = f"sudo docker stats --no-stream --format '{{{{.CPUPerc}}}}' {container} 2>/dev/null"
    output = run_cmd(cmd)
    try:
        return float(output.replace('%', '')) if output else 0.0
    except:
        return 0.0

def main():
    print("=" * 80)
    print("Real-time Bottleneck Metrics Collector")
    print("=" * 80)
    print(f"Collecting metrics every {COLLECTION_INTERVAL} seconds")
    print(f"Results: {METRICS_FILE}")
    print("Press Ctrl+C to stop")
    print("=" * 80)
    
    # CSV setup
    csv_file = open(METRICS_FILE, 'w', newline='')
    csv_writer = csv.writer(csv_file)
    csv_writer.writerow([
        'timestamp', 'kafka_total_messages', 'kafka_msg_rate',
        'spark_processed_rate', 'consumer_lag',
        'db_total_inserts', 'db_insert_rate',
        'ingestion_cpu', 'spark_cpu', 'kafka_cpu', 'db_cpu'
    ])
    csv_file.flush()
    
    prev_kafka_total = 0
    prev_db_total = 0
    start_time = time.time()
    
    try:
        while True:
            # Collect metrics
            kafka_total = get_kafka_total_messages()
            spark_rate = get_spark_metrics()
            consumer_lag = get_kafka_consumer_lag()
            db_total = get_db_write_rate()
            
            # Calculate rates
            kafka_rate = (kafka_total - prev_kafka_total) / COLLECTION_INTERVAL
            db_rate = (db_total - prev_db_total) / COLLECTION_INTERVAL
            
            # Get CPU metrics
            ingestion_cpu = get_container_cpu('industryflow-ingestion-service')
            spark_cpu = get_container_cpu('industryflow-spark-streaming')
            kafka_cpu = get_container_cpu('industryflow-kafka')
            db_cpu = get_container_cpu('industryflow-timescaledb')
            
            # Write to CSV
            csv_writer.writerow([
                datetime.now().isoformat(),
                kafka_total,
                round(kafka_rate, 1),
                round(spark_rate, 1),
                consumer_lag,
                db_total,
                round(db_rate, 1),
                round(ingestion_cpu, 1),
                round(spark_cpu, 1),
                round(kafka_cpu, 1),
                round(db_cpu, 1)
            ])
            csv_file.flush()
            
            # Console output
            elapsed = time.time() - start_time
            print(f"\n[{elapsed:.0f}s] Metrics:")
            print(f"  Kafka write rate: {kafka_rate:.1f} msg/s")
            print(f"  Spark process rate: {spark_rate:.1f} rows/s")
            print(f"  DB insert rate: {db_rate:.1f} rows/s")
            print(f"  Consumer lag: {consumer_lag:,}")
            print(f"  CPU - Ingestion: {ingestion_cpu:.1f}% | Spark: {spark_cpu:.1f}% | Kafka: {kafka_cpu:.1f}% | DB: {db_cpu:.1f}%")
            
            # Update previous values
            prev_kafka_total = kafka_total
            prev_db_total = db_total
            
            time.sleep(COLLECTION_INTERVAL)
            
    except KeyboardInterrupt:
        print("\n\n✓ Metrics collection stopped")
    finally:
        csv_file.close()
        print(f"✓ Results saved to: {METRICS_FILE}")

if __name__ == '__main__':
    main()
