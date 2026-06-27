# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# Notebook hub image (ADR-0011/0012/0014). REFERENCE — not yet cluster-validated.
FROM python:3.14-slim

WORKDIR /srv/jupyterhub

# configurable-http-proxy is run as a separate container (see the Helm chart); the hub image
# needs only JupyterHub + KubeSpawner.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY identity.py jupyterhub_config.py ./

# Run as a non-root user (uid 10001); the hub writes its runtime state (sqlite, cookie secret)
# under the workdir, so make it writable to that user.
RUN useradd -u 10001 -r -s /usr/sbin/nologin hub \
 && chown -R 10001:10001 /srv/jupyterhub
USER 10001

EXPOSE 8081
CMD ["jupyterhub", "-f", "/srv/jupyterhub/jupyterhub_config.py"]
