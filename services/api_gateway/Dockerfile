# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

FROM python:3.14-slim

WORKDIR /app

# SECURITY_REFRESH is a cache epoch the workflow advances daily (.github/workflows/images.yml). pip
# and its vendored certifi come from the base image, and the pins below are direct only, so pip and
# each transitive dependency are taken fresh — but only when the layer re-runs, and the persistent
# build cache replays it verbatim. This RUN moves the cache key of every layer after it, so a security
# release of pip or a transitive dependency lands within a day.
ARG SECURITY_REFRESH=unset
RUN echo "security refresh: ${SECURITY_REFRESH}" && pip install --no-cache-dir --upgrade pip

# Copy requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The platform's shared tenant-naming and window contract (libs/industryflow_contracts; ADR-0003
# dec 5-7, ADR-0006 dec 7), supplied as the named build context `contracts`.
COPY --from=contracts . /tmp/industryflow-contracts
RUN pip install --no-cache-dir /tmp/industryflow-contracts && rm -rf /tmp/industryflow-contracts

# Copy API code (.dockerignore keeps tests, dev pins, caches and .env out)
COPY . .

# Run as a non-root user (uid 10001). Listens on an unprivileged port; writes nothing to the
# image filesystem (so it runs fine with a read-only root FS + a writable /tmp).
RUN useradd -u 10001 -r -s /usr/sbin/nologin appuser \
 && mkdir -p /tmp/prometheus-multiproc && chown 10001:10001 /tmp/prometheus-multiproc
USER 10001

# Expose port
EXPOSE 8000

# Run API server. API_GATEWAY_WORKERS sets the uvicorn worker count per container; each worker
# holds its own DB pool, so it is part of the connection budget (database.py).
ENV API_GATEWAY_WORKERS=2
# Request metrics from every uvicorn worker, aggregated at /metrics: without a shared directory
# each scrape reads one worker's counters. Emptied at start so a restart drops stale files; also
# created above, so importing the app outside this CMD (smoke tests) finds it.
ENV PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus-multiproc
CMD ["sh", "-c", "rm -rf \"$PROMETHEUS_MULTIPROC_DIR\" && mkdir -p \"$PROMETHEUS_MULTIPROC_DIR\" && exec uvicorn main:app --host 0.0.0.0 --port 8000 --workers \"$API_GATEWAY_WORKERS\""]