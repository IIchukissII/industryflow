# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The infrastructure agent's tools, served over MCP (ADR-0045 dec 2).

Read-only, and over the operator's monitoring plane only (ADR-0046 dec 2, 7): the metrics store, the
log store, the alert router and the runbooks. Any MCP client can use them; the agent launches this
module over stdio. Results are capped, because they are evidence for one note, not an export.
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer

import runbooks
from config import Config

MAX_SERIES = 10
MAX_POINTS = 120
MAX_LINES = 60
MAX_LINE_CHARS = 500
# What counts as an error line in a service's log.
ERROR_FILTER = r'|~ "(?i)(error|exception|traceback|fatal|refused|failed|timeout|denied)"'


def _cut(line: str) -> str:
    """A line within MAX_LINE_CHARS, cut at a space: a value cut in half would reach the redactor
    as a fragment it cannot recognise."""
    if len(line) <= MAX_LINE_CHARS:
        return line
    head = line[:MAX_LINE_CHARS]
    return (head.rsplit(" ", 1)[0] if " " in head else "") + " …"


def _reported(fn: Callable[..., Awaitable[dict[str, Any]]]) -> Callable[..., Awaitable[dict[str, Any]]]:
    """A failure comes back as data with its cause: the MCP layer would otherwise replace it with a
    generic message, and the note would say only that the store was unavailable."""
    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return await fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 — reported to the caller as evidence
            return {"error": f"{type(exc).__name__}: {exc}"}
    return wrapper


def build_server(config: Config, transport: httpx.AsyncBaseTransport | None = None) -> MCPServer:
    server = MCPServer("industryflow-ops")
    rows = runbooks.load(config.runbooks_dir) if config.runbooks_dir.is_dir() else []
    alert_rows = runbooks.load_alerts(config.runbooks_dir) if config.runbooks_dir.is_dir() else {}

    def client() -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=15.0, transport=transport)

    @server.tool()
    @_reported
    async def prometheus_range(query: str, start: str, end: str, step: str = "60s") -> dict[str, Any]:
        """A PromQL range query over the platform's metrics; at most 10 series of 120 points."""
        async with client() as http:
            r = await http.get(f"{config.prometheus_url}/api/v1/query_range",
                               params={"query": query, "start": start, "end": end, "step": step})
        r.raise_for_status()
        result = r.json().get("data", {}).get("result", [])
        return {"series": [{"metric": s.get("metric", {}), "values": s.get("values", [])[-MAX_POINTS:]}
                           for s in result[:MAX_SERIES]],
                "truncated": len(result) > MAX_SERIES}

    @server.tool()
    @_reported
    async def loki_errors(service: str, start: str, end: str, limit: int = 40) -> dict[str, Any]:
        """Error lines from one service's logs between start and end, newest first."""
        selector = config.log_selector % service.replace('"', "")
        async with client() as http:
            r = await http.get(f"{config.loki_url}/loki/api/v1/query_range",
                               params={"query": f"{selector} {ERROR_FILTER}", "start": start, "end": end,
                                       "limit": min(limit, MAX_LINES), "direction": "backward"})
        r.raise_for_status()
        lines = [{"ts": ts, "line": _cut(line)}
                 for stream in r.json().get("data", {}).get("result", [])
                 for ts, line in stream.get("values", [])]
        lines.sort(key=lambda x: x["ts"], reverse=True)
        return {"service": service, "lines": lines[:MAX_LINES]}

    @server.tool()
    @_reported
    async def active_alerts() -> dict[str, Any]:
        """The alerts the operator's alert router holds now (its own state, never tenant alerts)."""
        async with client() as http:
            r = await http.get(f"{config.alertmanager_url}/api/v2/alerts", params={"active": "true"})
        r.raise_for_status()
        return {"alerts": [{"alertname": a.get("labels", {}).get("alertname"), "labels": a.get("labels", {}),
                            "startsAt": a.get("startsAt"), "state": a.get("status", {}).get("state")}
                           for a in r.json()[:50]]}

    @server.tool()
    @_reported
    async def alert_runbook(alertname: str) -> dict[str, Any]:
        """The alert table's row for one alert, by its exact name: when it fires and how to handle it."""
        row = alert_rows.get(alertname)
        return {"row": vars(row) if row else None}

    @server.tool()
    @_reported
    async def runbook_rows(text: str, limit: int = 3) -> dict[str, Any]:
        """The runbook rows (symptom, cause, fix) that best match a description of the problem."""
        return {"rows": [vars(row) for row in runbooks.match(rows, text, limit)]}

    return server


if __name__ == "__main__":
    build_server(Config.from_env()).run("stdio")
