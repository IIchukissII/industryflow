# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# The tenant-delete job (ADR-0033 dec 4): one pass over the requested deletions, then exit.
FROM python:3.14-slim

LABEL service="tenant-delete"

WORKDIR /app

# SECURITY_REFRESH is a cache epoch the workflow advances daily (.github/workflows/images.yml). pip
# and its vendored certifi come from the base image, and the pins below are direct only, so pip and
# each transitive dependency are taken fresh — but only when the layer re-runs, and the persistent
# build cache replays it verbatim. This RUN moves the cache key of every layer after it, so a security
# release of pip or a transitive dependency lands within a day.
ARG SECURITY_REFRESH=unset
RUN echo "security refresh: ${SECURITY_REFRESH}" && pip install --no-cache-dir --upgrade pip

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . ./tenant_delete/

RUN useradd -u 10001 -r -s /usr/sbin/nologin appuser
USER 10001

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python", "-m", "tenant_delete.main"]
