# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

FROM python:3.14-slim

WORKDIR /app

# Copy requirements and install
COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all API code
COPY api/config.py .
COPY api/auth.py .
COPY api/models.py .
COPY api/repository.py .
COPY api/api.py .
COPY api/routers/ ./routers/

# Run as a non-root user (uid 10001).
RUN useradd -u 10001 -r -s /usr/sbin/nologin appuser
USER 10001

# Expose API port
EXPOSE 8001

# Run API server with 4 workers
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "4"]
