#!/bin/bash

# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

echo "=== BOTTLENECK ANALYSIS ==="
echo ""
echo "1. KAFKA METRICS (messages in topic):"
sudo docker exec industryflow-kafka kafka-run-class kafka.tools.GetOffsetShell \
  --broker-list localhost:9092 \
  --topic sensor-data-raw \
  --time -1 | awk -F: '{sum += $3} END {print "Total messages in Kafka: " sum}'

echo ""
echo "2. SPARK PROCESSING RATE:"
sudo docker logs industryflow-spark-streaming 2>&1 | \
  grep "processedRowsPerSecond" | tail -1 | \
  grep -oP '"processedRowsPerSecond" : \K[0-9.]+'

echo ""
echo "3. KAFKA PRODUCER METRICS (from ingestion service):"
sudo docker exec industryflow-ingestion-service ps aux | grep -c python

echo ""
echo "4. DATABASE ACTIVE CONNECTIONS:"
sudo docker exec industryflow-timescaledb psql -U postgres -d industryflow -c \
  "SELECT count(*) as active_connections FROM pg_stat_activity WHERE state = 'active';"

echo ""
echo "5. KAFKA LAG PER PARTITION:"
sudo docker exec industryflow-kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 \
  --describe --all-groups 2>/dev/null | grep sensor-data-raw

echo ""
echo "6. CONTAINER RESOURCE USAGE:"
sudo docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}" | \
  grep -E "NAME|ingestion|spark-streaming|kafka|timescale"
