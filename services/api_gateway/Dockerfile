# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

FROM python:3.14-slim

WORKDIR /app

# Copy requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy API code
COPY . .

# Run as a non-root user (uid 10001). Listens on an unprivileged port; writes nothing to the
# image filesystem (so it runs fine with a read-only root FS + a writable /tmp).
RUN useradd -u 10001 -r -s /usr/sbin/nologin appuser
USER 10001

# Expose port
EXPOSE 8000

# Run API server
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "8"]