# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

FROM python:3.14-slim

LABEL maintainer="IndustryFlow Team"
LABEL service="ml-service-api"

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip
RUN pip install --no-cache-dir --upgrade pip

# Copy requirements and install dependencies
COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The platform's shared tenant-naming and window contract (libs/industryflow_contracts; ADR-0003
# dec 5-7, ADR-0006 dec 7), supplied as the named build context `contracts`.
COPY --from=contracts . /tmp/industryflow-contracts
RUN pip install --no-cache-dir /tmp/industryflow-contracts && rm -rf /tmp/industryflow-contracts

# Copy API application code
COPY api/ .

# Create models directory, owned by the non-root runtime user (models are written at runtime).
RUN mkdir -p /app/models \
 && useradd -u 10001 -r -s /usr/sbin/nologin appuser \
 && chown -R 10001:10001 /app/models \
 && mkdir -p /tmp/prometheus-multiproc && chown 10001:10001 /tmp/prometheus-multiproc
USER 10001

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Expose port
EXPOSE 8002

# Run with uvicorn. Worker count x the DB pool size must stay within the DB role's
# connection limit (ml_service_user = 10): 2 workers x max 4 = 8.
# Request metrics from every uvicorn worker, aggregated at /metrics: without a shared directory
# each scrape reads one worker's counters. Emptied at start so a restart drops stale files; also
# created above, so importing the app outside this CMD (smoke tests) finds it.
ENV PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus-multiproc
CMD ["sh", "-c", "rm -rf \"$PROMETHEUS_MULTIPROC_DIR\" && mkdir -p \"$PROMETHEUS_MULTIPROC_DIR\" && exec uvicorn main:app --host 0.0.0.0 --port 8002 --workers 2"]
