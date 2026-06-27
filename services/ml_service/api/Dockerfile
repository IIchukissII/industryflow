# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

FROM python:3.11-slim

LABEL maintainer="IndustryFlow Team"
LABEL service="ml-service-api"

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip
RUN pip install --no-cache-dir --upgrade pip

# Copy requirements and install dependencies
COPY api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy API application code
COPY api/ .

# Create models directory, owned by the non-root runtime user (models are written at runtime).
RUN mkdir -p /app/models \
 && useradd -u 10001 -r -s /usr/sbin/nologin appuser \
 && chown -R 10001:10001 /app/models
USER 10001

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Expose port
EXPOSE 8002

# Run with uvicorn. Worker count x the DB pool size must stay within the DB role's
# connection limit (ml_service_user = 10): 2 workers x max 4 = 8.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8002", "--workers", "2"]
