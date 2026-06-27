# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY config.py .
COPY dependencies.py .
COPY schemas.py .
COPY kafka_producer.py .
COPY main.py .

# Set Python path
ENV PYTHONPATH=/app

# Run as a non-root user (uid 10001).
RUN useradd -u 10001 -r -s /usr/sbin/nologin appuser
USER 10001

# Run application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8003"]
