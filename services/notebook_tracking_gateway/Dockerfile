# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# Experiment-tracking gateway image (ADR-0019). Authenticates a notebook's tracking capability and
# scopes every MLflow call to the caller's tenant; the live MLflow proxy + artifact pre-signing are
# cluster-validated (see README / issue #19).
FROM python:3.14-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Every module the app imports. An omission here does not fail a test — it ships an image that
# crashloops on `import`, which is why `tests/test_image_contents.py` checks this list against the
# directory rather than trusting that it was remembered.
COPY policy.py scoping.py gateway.py admission.py staging.py ./

# Run as a non-root user (uid 10001).
RUN useradd -u 10001 -r -s /usr/sbin/nologin appuser
USER 10001

EXPOSE 5050
CMD ["python", "-m", "gateway"]
