# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The infrastructure agent's service (ADR-0046): a receiver for the operator's alert router.

The router posts its webhook here with the shared secret as a bearer token (dec 1). Each firing
alert, once per firing, runs the graph in the background; the router gets its answer at once and
never waits on a model. Resolved alerts are not explained.

Once per firing: a firing is its fingerprint and start, remembered for longer than the router's
repeat interval, and checked against the log store after a restart. A storm is bounded: at most
MAX_PER_GROUP alerts of one webhook, critical first, and at most MAX_PENDING waiting in all; the rest
are logged as not explained, so an outage that fires everything does not become a model bill.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import sys
import time
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

import graph
from config import Config
from model import writer as make_writer
from publish import LokiPublisher

log = logging.getLogger("ops_agent")
CONCURRENCY = 2
MAX_PER_GROUP = 5
MAX_PENDING = 10
# Longer than any repeat interval an operator would set (the shipped one is 4h).
SEEN_TTL_SECONDS = 7 * 86_400
MAX_SEEN = 10_000
_SEVERITY = {"critical": 0, "warning": 1}
# What the tool server may see: endpoints and paths, never a secret (ADR-0046 dec 7).
_TOOL_ENV = ("OPS_AGENT_PROMETHEUS_URL", "OPS_AGENT_LOKI_URL", "OPS_AGENT_ALERTMANAGER_URL",
             "OPS_AGENT_LOG_SELECTOR", "OPS_AGENT_RUNBOOKS_DIR")


class McpTools:
    """The agent's side of its MCP tools: a stdio subprocess by default, or a server at a URL."""

    def __init__(self, target: Any):
        self._target = target
        self._stack = AsyncExitStack()
        self._client: Any = None
        self._lock = asyncio.Lock()

    @classmethod
    def from_config(cls, config: Config) -> "McpTools":
        from mcp import StdioServerParameters
        if config.tools == "stdio":
            env = {k: os.environ[k] for k in _TOOL_ENV if k in os.environ}
            env["PYTHONPATH"] = os.path.dirname(os.path.abspath(__file__))
            return cls(StdioServerParameters(command=sys.executable, args=["-m", "tools"], env=env))
        return cls(config.tools)

    async def __aenter__(self) -> "McpTools":
        from mcp import Client
        self._client = await self._stack.enter_async_context(Client(self._target))
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self._stack.aclose()

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            result = await self._client.call_tool(name, arguments)
        text = "".join(getattr(part, "text", "") for part in result.content)
        if result.is_error:
            raise RuntimeError(text or f"tool {name} failed")
        return json.loads(text) if text else {}


async def _never(key: str) -> bool:
    return False


def create_app(config: Config, tools: Any = None, writer: Any = None, publisher: Any = None) -> FastAPI:
    seen: dict[str, float] = {}
    gate = asyncio.Semaphore(CONCURRENCY)
    tasks: set[asyncio.Task] = set()
    state: dict[str, Any] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with AsyncExitStack() as stack:
            active_tools = tools or await stack.enter_async_context(McpTools.from_config(config))
            active_publisher = publisher or LokiPublisher(config)
            state["published"] = getattr(active_publisher, "published", None) or _never
            state["graph"] = graph.build(config, active_tools, writer or make_writer(config), active_publisher)
            yield
            for task in list(tasks):
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    app = FastAPI(title="IndustryFlow infrastructure agent", lifespan=lifespan, docs_url=None, redoc_url=None)

    async def explain(alert: dict[str, Any], key: str) -> None:
        async with gate:
            name = alert.get("labels", {}).get("alertname", "unnamed")
            try:
                if await state["published"](key):
                    log.info("a note for %s already exists; not explained again", name)
                    return
                await state["graph"].ainvoke({"alert": alert})
                log.info("note published for %s", name)
            except Exception:  # noqa: BLE001 — one alert's failure never stops the receiver
                log.exception("no note for %s", name)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/alertmanager", status_code=202)
    async def receive(request: Request, authorization: str = Header(default="")) -> JSONResponse:
        expected = f"Bearer {config.webhook_secret}"
        if not hmac.compare_digest(authorization.encode(), expected.encode()):
            raise HTTPException(status_code=401, detail="not authorized")
        payload = await request.json()
        now = time.time()
        for key in [k for k, t in seen.items() if now - t > SEEN_TTL_SECONDS]:
            del seen[key]
        firing = [a for a in payload.get("alerts", []) if a.get("status") == "firing"]
        firing.sort(key=lambda a: _SEVERITY.get(a.get("labels", {}).get("severity", ""), 2))
        accepted, skipped = 0, 0
        for alert in firing:
            key = f"{alert.get('fingerprint', '')}@{alert.get('startsAt', '')}"
            if key in seen:
                continue
            if accepted >= MAX_PER_GROUP or len(tasks) >= MAX_PENDING:
                skipped += 1
                continue
            seen[key] = now
            task = asyncio.create_task(explain(alert, key))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
            accepted += 1
        while len(seen) > MAX_SEEN:
            del seen[next(iter(seen))]
        if skipped:
            log.warning("%d firing alerts not explained: at most %d per webhook and %d waiting",
                        skipped, MAX_PER_GROUP, MAX_PENDING)
        return JSONResponse({"accepted": accepted, "skipped": skipped}, status_code=202)

    return app


def main() -> None:
    import uvicorn
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    config = Config.from_env()
    problems = config.refusals()
    if problems:
        for problem in problems:
            log.error("refusing to start: %s", problem)
        raise SystemExit(2)
    uvicorn.run(create_app(config), host="0.0.0.0", port=int(os.environ.get("OPS_AGENT_PORT", "8095")),
                log_level="info", access_log=False)


if __name__ == "__main__":
    main()
