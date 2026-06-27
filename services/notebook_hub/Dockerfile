# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# Notebook hub image (ADR-0011/0012/0014/0018). Serves both deployment profiles: KubeSpawner on
# Kubernetes (chp runs as a sibling container per the Helm chart) and DockerSpawner on Compose
# (the hub runs the proxy in-process, so the chp binary is installed here). NOTEBOOK_SPAWNER picks.
FROM python:3.12-slim

WORKDIR /srv/jupyterhub

# Node + configurable-http-proxy so JupyterHub can run the proxy in-process on the Compose profile
# (no separate chp container). Unused on k8s, where chp is a sibling.
RUN apt-get update && \
    apt-get install -y --no-install-recommends nodejs npm && \
    npm install -g configurable-http-proxy@4.6.3 && \
    npm cache clean --force && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# capabilities.py is imported by the config to mint per-session handles (ADR-0015).
COPY identity.py capabilities.py jupyterhub_config.py ./

# Run as a non-root user (uid 10001); the hub writes its runtime state (sqlite, cookie secret)
# under the workdir, so make it writable to that user. On the DockerSpawner profile the hub also
# needs to reach the Docker socket — granted by a supplementary group (compose `group_add` with the
# host's docker gid), NOT by running the hub as root.
RUN useradd -u 10001 -r -s /usr/sbin/nologin hub \
 && chown -R 10001:10001 /srv/jupyterhub
USER 10001

# 8081 = hub API; 8000 = public proxy (chp) when the hub manages it (DockerSpawner profile).
EXPOSE 8000 8081
CMD ["jupyterhub", "-f", "/srv/jupyterhub/jupyterhub_config.py"]
