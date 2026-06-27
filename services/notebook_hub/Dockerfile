# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# Notebook hub image (ADR-0011/0012/0014). REFERENCE — not yet cluster-validated.
FROM python:3.12-slim

WORKDIR /srv/jupyterhub

# configurable-http-proxy is run as a separate container (see the Helm chart); the hub image
# needs only JupyterHub + KubeSpawner.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY identity.py jupyterhub_config.py ./

EXPOSE 8081
CMD ["jupyterhub", "-f", "/srv/jupyterhub/jupyterhub_config.py"]
