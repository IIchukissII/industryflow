# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# Cold-layer exporter (ADR-0025): a single-node columnar writer, not Spark (decision 11). Reads
# raw measurements from TimescaleDB and writes partitioned Parquet to the object store, then drops
# the exported chunk. Run on a schedule by a Kubernetes CronJob (decision 9).
FROM python:3.14-slim

LABEL service="cold-export"

WORKDIR /app

# SECURITY_REFRESH is a cache epoch the workflow advances daily (.github/workflows/images.yml). pip
# and its vendored certifi come from the base image, and the pins below are direct only, so pip and
# each transitive dependency are taken fresh — but only when the layer re-runs, and the persistent
# build cache replays it verbatim. This RUN moves the cache key of every layer after it, so a security
# release of pip or a transitive dependency lands within a day.
ARG SECURITY_REFRESH=unset
RUN echo "security refresh: ${SECURITY_REFRESH}" && pip install --no-cache-dir --upgrade pip

# pyarrow and psycopg2-binary ship as manylinux wheels — no compiler needed.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The platform's shared tenant-naming and window contract (libs/industryflow_contracts; ADR-0003
# dec 5-7, ADR-0006 dec 7), supplied as the named build context `contracts`.
COPY --from=contracts . /tmp/industryflow-contracts
RUN pip install --no-cache-dir /tmp/industryflow-contracts && rm -rf /tmp/industryflow-contracts

# Ship the package so relative imports resolve under `python -m cold_export.main`.
COPY . ./cold_export/

# Non-root runtime (uid 10001, matching the other service images). The exporter connects to the
# DB over TCP and to the object store over HTTP — no local DB privileges, no writable app dir.
RUN useradd -u 10001 -r -s /usr/sbin/nologin appuser
USER 10001

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python", "-m", "cold_export.main"]
