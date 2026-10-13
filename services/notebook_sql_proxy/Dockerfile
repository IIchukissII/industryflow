# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# SQL access proxy image (ADR-0015). The module list below is explicit; tests/test_image_contents.py
# fails if a module in this directory is missing from it.
FROM python:3.14-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The platform's shared tenant-naming and window contract (libs/industryflow_contracts; ADR-0003
# dec 5-7, ADR-0006 dec 7), supplied as the named build context `contracts`.
COPY --from=contracts . /tmp/industryflow-contracts
RUN pip install --no-cache-dir /tmp/industryflow-contracts && rm -rf /tmp/industryflow-contracts

COPY binding.py proxy.py wire.py scram.py server.py ./

# Run as a non-root user (uid 10001).
RUN useradd -u 10001 -r -s /usr/sbin/nologin appuser
USER 10001

# Listens for kernel connections; configured via SQL_PROXY_* env (see server.main()).
EXPOSE 6432
CMD ["python", "-m", "server"]
