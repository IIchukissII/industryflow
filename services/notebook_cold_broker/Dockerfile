# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# Cold-store broker image (ADR-0025 dec 5, read side). Authenticates a notebook's cold capability
# and vends read-only, tenant-prefix-scoped pre-signed GET URLs for the cold layer. Holds a
# read-only cold-bucket principal; the live S3 pre-signing is cluster-validated.
FROM python:3.14-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY policy.py broker.py ./

# Run as a non-root user (uid 10001).
RUN useradd -u 10001 -r -s /usr/sbin/nologin appuser
USER 10001

EXPOSE 5060
CMD ["python", "-m", "broker"]
