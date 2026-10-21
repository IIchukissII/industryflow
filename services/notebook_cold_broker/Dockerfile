# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# Cold-store broker image (ADR-0025 dec 5, read side). Authenticates a notebook's cold capability
# and vends read-only, tenant-prefix-scoped pre-signed GET URLs for the cold layer. Holds a
# read-only cold-bucket principal; the live S3 pre-signing is cluster-validated.
FROM python:3.14-slim

WORKDIR /app
# SECURITY_REFRESH is a cache epoch the workflow advances daily (.github/workflows/images.yml). The
# pins below are direct only, so the resolve picks each transitive dependency (certifi, urllib3, ...)
# fresh — but only when the layer re-runs, and the persistent build cache replays it verbatim. This
# RUN moves the cache key of every layer after it, so a transitive security release lands within a day.
ARG SECURITY_REFRESH=unset
RUN echo "security refresh: ${SECURITY_REFRESH}"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The platform's shared tenant-naming and window contract (libs/industryflow_contracts; ADR-0003
# dec 5-7, ADR-0006 dec 7), supplied as the named build context `contracts`.
COPY --from=contracts . /tmp/industryflow-contracts
RUN pip install --no-cache-dir /tmp/industryflow-contracts && rm -rf /tmp/industryflow-contracts

COPY policy.py broker.py ./

# Run as a non-root user (uid 10001).
RUN useradd -u 10001 -r -s /usr/sbin/nologin appuser
USER 10001

EXPOSE 5060
CMD ["python", "-m", "broker"]
