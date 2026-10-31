# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Configuration from the environment. Endpoints are configuration (ADR-0046 dec 10), so the same
image reaches the single-host stack by its service names and an operator's cluster stack by theirs.
Secrets are read from the environment, which the deployment injects; none has a default."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Config:
    webhook_secret: str
    prometheus_url: str
    loki_url: str
    alertmanager_url: str
    log_selector: str
    runbooks_dir: Path
    window_minutes: int
    model_provider: str
    model: str
    model_base_url: str
    model_api_key: str
    effort: str
    tools: str

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            webhook_secret=_env("OPS_AGENT_WEBHOOK_SECRET"),
            prometheus_url=_env("OPS_AGENT_PROMETHEUS_URL", "http://prometheus:9090").rstrip("/"),
            loki_url=_env("OPS_AGENT_LOKI_URL", "http://loki:3100").rstrip("/"),
            alertmanager_url=_env("OPS_AGENT_ALERTMANAGER_URL", "http://alertmanager:9093").rstrip("/"),
            # How a service's logs are selected in the log store; `%s` is the service name. The
            # single-host stack labels container logs by their Compose service.
            log_selector=_env("OPS_AGENT_LOG_SELECTOR", '{service="%s"}'),
            runbooks_dir=Path(_env("OPS_AGENT_RUNBOOKS_DIR", "/app/runbooks")),
            window_minutes=int(_env("OPS_AGENT_WINDOW_MINUTES", "30")),
            # `anthropic` (the Anthropic SDK) or `openai-compatible` (Mistral's API, or a model
            # server on the operator's own host) — ADR-0045 dec 3.
            model_provider=_env("OPS_AGENT_MODEL_PROVIDER", "anthropic"),
            model=_env("OPS_AGENT_MODEL", "claude-opus-5-5"),
            model_base_url=_env("OPS_AGENT_MODEL_BASE_URL"),
            model_api_key=_env("OPS_AGENT_MODEL_API_KEY"),
            effort=_env("OPS_AGENT_EFFORT", "medium"),
            # `stdio` launches the tool server as a subprocess; a URL reaches one over HTTP.
            tools=_env("OPS_AGENT_TOOLS", "stdio"),
        )

    def refusals(self) -> list[str]:
        """Why this configuration cannot run; empty when it can. The agent is off unless the operator
        enables it (ADR-0045 dec 8), and an unauthenticated receiver is refused (ADR-0046 dec 1)."""
        problems = []
        if len(self.webhook_secret) < 32:
            problems.append("OPS_AGENT_WEBHOOK_SECRET is unset or shorter than 32 characters")
        if self.model_provider not in ("anthropic", "openai-compatible"):
            problems.append(f"OPS_AGENT_MODEL_PROVIDER {self.model_provider!r} is not anthropic or openai-compatible")
        if self.model_provider == "openai-compatible" and not self.model_base_url:
            problems.append("OPS_AGENT_MODEL_BASE_URL is required for an openai-compatible provider")
        return problems
