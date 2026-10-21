# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

FROM python:3.14-slim

WORKDIR /app

# SECURITY_REFRESH is a cache epoch the workflow advances daily (.github/workflows/images.yml). The
# pins below are direct only, so the resolve picks each transitive dependency (certifi, urllib3, ...)
# fresh — but only when the layer re-runs, and the persistent build cache replays it verbatim. This
# RUN moves the cache key of every layer after it, so a transitive security release lands within a day.
ARG SECURITY_REFRESH=unset
RUN echo "security refresh: ${SECURITY_REFRESH}"

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The platform's shared tenant-naming and window contract (libs/industryflow_contracts; ADR-0003
# dec 5-7, ADR-0006 dec 7), supplied as the named build context `contracts`.
COPY --from=contracts . /tmp/industryflow-contracts
RUN pip install --no-cache-dir /tmp/industryflow-contracts && rm -rf /tmp/industryflow-contracts

# Copy application code
COPY config.py .
COPY dependencies.py .
COPY schemas.py .
COPY kafka_producer.py .
COPY main.py .

# Set Python path
ENV PYTHONPATH=/app

# Run as a non-root user (uid 10001).
RUN useradd -u 10001 -r -s /usr/sbin/nologin appuser \
 && mkdir -p /tmp/prometheus-multiproc && chown 10001:10001 /tmp/prometheus-multiproc
USER 10001

# Request metrics from every uvicorn worker, aggregated at /metrics: without a shared directory
# each scrape reads one worker's counters. Emptied at start so a restart drops stale files; also
# created above, so importing the app outside this CMD (smoke tests) finds it.
ENV PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus-multiproc
ENV INGESTION_WORKERS=1
CMD ["sh", "-c", "rm -rf \"$PROMETHEUS_MULTIPROC_DIR\" && mkdir -p \"$PROMETHEUS_MULTIPROC_DIR\" && exec uvicorn main:app --host 0.0.0.0 --port 8003 --workers \"$INGESTION_WORKERS\""]
