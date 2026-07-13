# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# HELD AT 3.11 WHILE THE SERVICES MOVED TO 3.14 (#211) — mlflow does not run on 3.14 yet. Not a
# packaging problem: mlflow is pure-python and resolves cleanly, so no wheel or lock check can see
# this. It is mlflow's own source. `mlflow/assistant/skill_installer.py` does
#
#     from importlib.abc import Traversable
#
# and 3.14 REMOVED importlib.abc.Traversable (deprecated since 3.12). The tracking server therefore
# dies on import, which the live tracking-gateway proof caught the moment this image was moved.
# 3.14.0 is the newest mlflow and no release yet claims 3.14. Only the SERVER is affected — the
# mlflow CLIENT imports fine on 3.14, so ml_service and the tracking gateway are already there.
# Tracked in #222; lift this when mlflow ships 3.14 support.
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
