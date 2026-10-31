# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Notes go into the operator's own log store only (ADR-0046 dec 4): one stream, `{job="ops-agent"}`,
from which the provisioned Incidents dashboard lists the notes and the dashboards draw them as
annotations. The agent holds no dashboard credential (dec 7). Notes are derivable and not backed up."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from config import Config

STREAM = {"job": "ops-agent"}


class LokiPublisher:
    def __init__(self, config: Config, transport: httpx.AsyncBaseTransport | None = None):
        self._url = f"{config.loki_url}/loki/api/v1/push"
        self._transport = transport

    async def publish(self, note: dict[str, Any]) -> None:
        stream = {**STREAM, "alertname": note["alertname"], "severity": note["severity"]}
        async with httpx.AsyncClient(timeout=15.0, transport=self._transport) as http:
            r = await http.post(self._url, json={"streams": [{
                "stream": stream, "values": [[str(time.time_ns()), json.dumps(note, sort_keys=True)]]}]})
        r.raise_for_status()
