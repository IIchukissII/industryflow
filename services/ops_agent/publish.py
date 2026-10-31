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

    async def published(self, key: str, days: int = 7) -> bool:
        """Whether a note for this firing is already in the log store, so a restarted agent does not
        explain it again. An unreadable store answers no: a second note beats none."""
        now = time.time_ns()
        params = {"query": f'{{job="ops-agent"}} |= `{key}`', "start": str(now - days * 86_400 * 10**9),
                  "end": str(now), "limit": "1"}
        try:
            async with httpx.AsyncClient(timeout=10.0, transport=self._transport) as http:
                r = await http.get(self._url.replace("/push", "/query_range"), params=params)
            r.raise_for_status()
            return any(s.get("values") for s in r.json().get("data", {}).get("result", []))
        except (httpx.HTTPError, ValueError):
            return False

    async def publish(self, note: dict[str, Any]) -> None:
        stream = {**STREAM, "alertname": note["alertname"], "severity": note["severity"]}
        async with httpx.AsyncClient(timeout=15.0, transport=self._transport) as http:
            r = await http.post(self._url, json={"streams": [{
                "stream": stream, "values": [[str(time.time_ns()), json.dumps(note, sort_keys=True)]]}]})
        r.raise_for_status()
