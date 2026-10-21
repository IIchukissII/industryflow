# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# Same interpreter as the services (#211). This image was held at 3.11 until mlflow 3.15.0 dropped
# its `importlib.abc.Traversable` import (removed in 3.14; the server died on import) and
# psycopg2-binary 2.9.13 shipped cp314 wheels (#222). A clean resolve proved nothing then: the
# image smoke imports `mlflow.server`, and the tracking-gateway proof does a real write.
FROM python:3.14-slim

# SECURITY_REFRESH is a cache epoch the workflow advances daily (.github/workflows/images.yml). pip
# and its vendored certifi come from the base image, outside the lock below, so pip is taken fresh —
# but only when the layer re-runs, and the persistent build cache replays it verbatim. This RUN moves
# the cache key of every layer after it, so a security release of pip lands within a day.
ARG SECURITY_REFRESH=unset
RUN echo "security refresh: ${SECURITY_REFRESH}" && pip install --no-cache-dir --upgrade pip

# The whole dependency closure is pinned in requirements.txt, not just mlflow/psycopg2/boto3.
# Pinning only the direct three left every transitive dep (sqlalchemy, alembic, pydantic, ...)
# floating, so two builds of the same commit could install different versions and a new release
# under MLflow could break the tracking server with nothing in this repo changing. See the header
# of requirements.txt for how to regenerate it after bumping a direct pin.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

# Run as a non-root user (uid 10001) with a writable home/workdir for any local state mlflow
# keeps (artifacts go to S3, backend to the DB).
RUN useradd -u 10001 -r -m -d /home/mlflow -s /usr/sbin/nologin mlflow
WORKDIR /home/mlflow
USER 10001

EXPOSE 5000

CMD ["mlflow", "server", "--host", "0.0.0.0", "--port", "5000"]
