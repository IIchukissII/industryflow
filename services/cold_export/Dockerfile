# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# Cold-layer exporter (ADR-0025): a single-node columnar writer, not Spark (decision 11). Reads
# raw measurements from TimescaleDB and writes partitioned Parquet to the object store, then drops
# the exported chunk. Run on a schedule by a Kubernetes CronJob (decision 9).
FROM python:3.12-slim

LABEL service="cold-export"

WORKDIR /app

# pyarrow and psycopg2-binary ship as manylinux wheels — no compiler needed.
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# Ship the package so relative imports resolve under `python -m cold_export.main`.
COPY . ./cold_export/

# Non-root runtime (uid 10001, matching the other service images). The exporter connects to the
# DB over TCP and to the object store over HTTP — no local DB privileges, no writable app dir.
RUN useradd -u 10001 -r -s /usr/sbin/nologin appuser
USER 10001

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python", "-m", "cold_export.main"]
