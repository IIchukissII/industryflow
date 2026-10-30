# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# The end-to-end suite's runner (ADR-0026 dec 8, 10): it drives the deployed stack through its public
# edges and reaches the environment's internals (a suspension's age, the deletion job, the device CA)
# through the host's Docker socket. Built and run by scripts/box-e2e.sh on the environment's host.
FROM docker:29-cli@sha256:5e6baf67325a79f64ed3bed6863954d5e05d0f3d4e57cbdb14809346814bbfdb

RUN apk add --no-cache bash openssl python3 py3-pip
COPY requirements.txt /tmp/e2e-requirements.txt
RUN pip install --no-cache-dir --break-system-packages -r /tmp/e2e-requirements.txt
WORKDIR /repo
ENTRYPOINT ["python3", "-m", "pytest"]
