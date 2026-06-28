# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# Experiment-tracking gateway image (ADR-0019). Authenticates a notebook's tracking capability and
# scopes every MLflow call to the caller's tenant; the live MLflow proxy + artifact pre-signing are
# cluster-validated (see README / issue #19).
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY policy.py scoping.py gateway.py ./

# Run as a non-root user (uid 10001).
RUN useradd -u 10001 -r -s /usr/sbin/nologin appuser
USER 10001

EXPOSE 5050
CMD ["python", "-m", "gateway"]
