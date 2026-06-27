# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# SQL access proxy image (ADR-0015). REFERENCE — the Postgres-wire backend is not implemented
# here (see README), so this image has no entrypoint yet.
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY binding.py proxy.py ./

# CMD is intentionally omitted until the Postgres-wire entry point exists.
