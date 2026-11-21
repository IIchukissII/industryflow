# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# The end-to-end suite's runner (ADR-0026 dec 8, 10): it drives the deployed stack through its public
# edges and reaches the environment's internals (a suspension's age, the deletion job, the device CA)
# through the host's Docker socket. Built and run by scripts/box-e2e.sh on the environment's host.
FROM docker:29-cli@sha256:34507eb5069d8be82c95dd3b6987d4250fd5fee49b82e2509b7d7a7030740d6d

RUN apk add --no-cache bash openssl python3 py3-pip
COPY requirements.txt /tmp/e2e-requirements.txt
RUN pip install --no-cache-dir --break-system-packages -r /tmp/e2e-requirements.txt
# The platform's shared contracts (libs/industryflow_contracts): the journeys send a device's reading
# in the shape a device sends it. Supplied as the named build context `contracts`.
COPY --from=contracts . /tmp/industryflow-contracts
RUN pip install --no-cache-dir --break-system-packages /tmp/industryflow-contracts && rm -rf /tmp/industryflow-contracts
WORKDIR /repo
ENTRYPOINT ["python3", "-m", "pytest"]
