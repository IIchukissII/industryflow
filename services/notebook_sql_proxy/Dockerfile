# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# SQL access proxy image (ADR-0015). The Postgres-wire entry point (server.py) is present; the
# live upstream relay is not yet cluster-validated (see README / issue #19).
FROM python:3.14-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY binding.py proxy.py wire.py scram.py server.py ./

# Run as a non-root user (uid 10001).
RUN useradd -u 10001 -r -s /usr/sbin/nologin appuser
USER 10001

# Listens for kernel connections; configured via SQL_PROXY_* env (see server.main()).
EXPOSE 6432
CMD ["python", "-m", "server"]
