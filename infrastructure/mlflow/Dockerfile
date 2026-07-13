# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

FROM python:3.14-slim

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
