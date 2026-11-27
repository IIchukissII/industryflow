# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# Tenant storage measurement (ADR-0049 dec 11): lists the object store and calls two database
# functions; writes and deletes nothing in either store. Run on a schedule.

FROM python:3.14-slim

LABEL service="tenant-usage"

WORKDIR /app

# SECURITY_REFRESH is a cache epoch the workflow advances daily (.github/workflows/images.yml). pip
# and its vendored certifi come from the base image, and the pins below are direct only, so pip and
# each transitive dependency are taken fresh — but only when the layer re-runs, and the persistent
# build cache replays it verbatim. This RUN moves the cache key of every layer after it, so a security
# release of pip or a transitive dependency lands within a day.
ARG SECURITY_REFRESH=unset
RUN echo "security refresh: ${SECURITY_REFRESH}" && pip install --no-cache-dir --upgrade pip

# psycopg2-binary and boto3 ship as wheels — no compiler needed.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The platform's shared tenant-naming and ownership contract (libs/industryflow_contracts; ADR-0039),
# supplied as the named build context `contracts`.
COPY --from=contracts . /tmp/industryflow-contracts
RUN pip install --no-cache-dir /tmp/industryflow-contracts && rm -rf /tmp/industryflow-contracts

# Ship the package so relative imports resolve under `python -m tenant_usage.main`.
COPY . ./tenant_usage/

# Non-root runtime (uid 10001, matching the other service images): TCP to the database, HTTP to the
# object store; no writable app dir.
RUN useradd -u 10001 -r -s /usr/sbin/nologin appuser
USER 10001

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python", "-m", "tenant_usage.main"]
