# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# The infrastructure agent (ADR-0045, ADR-0046): receives the operator's alert router, gathers evidence
# from the monitoring plane through its MCP tools, has a model write the note over an API, and
# publishes it to the operator's log store and dashboards. Holds no store credential.
FROM python:3.14-slim

WORKDIR /app
# SECURITY_REFRESH is a cache epoch the workflow advances daily (.github/workflows/images.yml); see
# services/notebook_cold_broker/Dockerfile for why this RUN exists.
ARG SECURITY_REFRESH=unset
RUN echo "security refresh: ${SECURITY_REFRESH}" && pip install --no-cache-dir --upgrade pip

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The runbooks the agent reads as a contract (ADR-0046 dec 9), supplied as the named build context
# `runbooks` (docs/operations).
COPY --from=runbooks . /app/runbooks

COPY config.py redaction.py runbooks.py tools.py model.py graph.py publish.py app.py ./

RUN useradd -u 10001 -r -s /usr/sbin/nologin appuser
USER 10001

EXPOSE 8095
CMD ["python", "-m", "app"]
