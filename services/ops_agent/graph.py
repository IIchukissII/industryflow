# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The infrastructure agent's graph: gather -> correlate -> redact -> explain -> publish.

Deterministic steps gather and shape the evidence; the model only writes from it (ADR-0045 dec 4).
Everything the model sees passes the redactor first (ADR-0045 dec 6). A step that cannot reach a
store records that as evidence rather than failing the note, and a model that cannot write leaves a
note built from the evidence alone, so an alert is never left without one.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol, TypedDict
from urllib.parse import parse_qs, urlparse

from langgraph.graph import END, START, StateGraph

from config import Config
from model import NoteWriter
from redaction import Redactor

log = logging.getLogger("ops_agent")

MAX_SERVICES = 4

SYSTEM = """You write incident notes for the operators of an industrial data platform.
You are given one platform alert and numbered evidence items gathered from the platform's metrics,
logs, alert router and runbooks. Write a short note with exactly these sections:

What happened: one or two sentences.
Impact: what is affected, as far as the evidence shows.
Likely cause: the most probable cause, citing evidence items like [E2]. If the evidence does not
support a cause, write "Unknown from the evidence" and say what would tell.
Next step: the runbook fix if one matches [E…], otherwise the first thing to check.

Rules: use only the evidence. Never invent numbers, names or times. Values like <tenant-1> or
<address-2> are redacted on purpose; keep them as they are. Plain text, no markdown headings,
at most 200 words."""


class Tools(Protocol):
    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


class Publisher(Protocol):
    async def publish(self, note: dict[str, Any]) -> None: ...


class State(TypedDict, total=False):
    alert: dict[str, Any]
    raw: list[dict[str, Any]]
    evidence: list[dict[str, str]]
    alert_text: str
    prompt: str
    redactions: dict[str, int]
    text: str
    model: str
    error: str


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: str | None) -> datetime:
    try:
        return datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)


def services_of(alert: dict[str, Any]) -> list[str]:
    """The services an alert points at: its rule's `services` annotation, then the scrape target's
    host. Ports and empty entries are dropped; order is kept."""
    names = [s.strip() for s in alert.get("annotations", {}).get("services", "").split(",")]
    instance = alert.get("labels", {}).get("instance", "")
    names.append(instance.rsplit(":", 1)[0] if instance else "")
    seen: list[str] = []
    for name in names:
        name = name.rsplit(":", 1)[0] if name.count(":") == 1 else name
        if name and name not in seen:
            seen.append(name)
    return seen[:MAX_SERVICES]


def expression_of(alert: dict[str, Any]) -> str:
    """The rule's PromQL, which the alert router passes along in the generator URL."""
    query = parse_qs(urlparse(alert.get("generatorURL", "")).query)
    return (query.get("g0.expr") or [""])[0]


def describe(alert: dict[str, Any]) -> str:
    labels, notes = alert.get("labels", {}), alert.get("annotations", {})
    parts = [labels.get("alertname", "unnamed alert"), f"severity {labels.get('severity', 'unknown')}",
             f"firing since {alert.get('startsAt', 'unknown')}"]
    for key in ("summary", "description"):
        if notes.get(key):
            parts.append(notes[key])
    extra = {k: v for k, v in labels.items() if k not in ("alertname", "severity", "platform")}
    if extra:
        parts.append("labels: " + ", ".join(f"{k}={v}" for k, v in sorted(extra.items())))
    return "; ".join(parts)


def _series_summary(series: dict[str, Any]) -> str:
    values = [float(v) for _, v in series.get("values", []) if v not in ("NaN", "+Inf", "-Inf")]
    metric = ", ".join(f"{k}={v}" for k, v in sorted(series.get("metric", {}).items()) if k != "platform")
    if not values:
        return f"{{{metric}}}: no samples"
    return (f"{{{metric}}}: {len(values)} samples, first {values[0]:g}, last {values[-1]:g}, "
            f"min {min(values):g}, max {max(values):g}")


def build(config: Config, tools: Tools, writer: NoteWriter, publisher: Publisher):
    async def gather(state: State) -> State:
        alert = state["alert"]
        start = _parse_time(alert.get("startsAt")) - timedelta(minutes=config.window_minutes)
        end = datetime.now(timezone.utc)
        window = {"start": _iso(start), "end": _iso(end)}
        requests: list[tuple[str, str, dict[str, Any]]] = []
        if expr := expression_of(alert):
            requests.append(("metrics", "prometheus_range", {"query": expr, **window}))
        for service in services_of(alert):
            requests.append((f"logs of {service}", "loki_errors", {"service": service, **window}))
        requests.append(("alert router", "active_alerts", {}))
        requests.append(("runbooks", "runbook_rows", {"text": describe(alert)}))
        raw = []
        for source, tool, args in requests:
            try:
                raw.append({"source": source, "tool": tool, "result": await tools.call(tool, args)})
            except Exception as exc:  # noqa: BLE001 — an unreachable store is itself evidence
                raw.append({"source": source, "tool": tool, "error": f"{type(exc).__name__}: {exc}"})
        return {"raw": raw}

    async def correlate(state: State) -> State:
        alert, items = state["alert"], []
        name = alert.get("labels", {}).get("alertname")
        for entry in state["raw"]:
            source = entry["source"]
            if "error" in entry:
                items.append((source, f"unavailable: {entry['error']}"))
                continue
            result = entry["result"]
            if entry["tool"] == "prometheus_range":
                lines = [_series_summary(s) for s in result.get("series", [])] or ["no series returned"]
                items.append((source, "; ".join(lines)))
            elif entry["tool"] == "loki_errors":
                logs = result.get("lines", [])
                if not logs:
                    items.append((source, "no error lines in the window"))
                else:
                    sample = " | ".join(x["line"] for x in logs[:8])
                    items.append((source, f"{len(logs)} error lines; newest first: {sample}"))
            elif entry["tool"] == "active_alerts":
                others = sorted({a["alertname"] for a in result.get("alerts", [])
                                 if a.get("alertname") and a["alertname"] != name})
                items.append((source, "other active alerts: " + (", ".join(others) if others else "none")))
            elif entry["tool"] == "runbook_rows":
                for row in result.get("rows", []):
                    items.append((f"runbook {row['source']}",
                                  f"symptom: {row['symptom']}; cause: {row['cause']}; fix: {row['fix']}"))
                if not result.get("rows"):
                    items.append((source, "no matching runbook row"))
        evidence = [{"id": f"E{n}", "source": s, "text": t} for n, (s, t) in enumerate(items, 1)]
        return {"evidence": evidence}

    async def redact(state: State) -> State:
        redactor = Redactor()
        alert_text = redactor(describe(state["alert"]))
        evidence = [{**e, "text": redactor(e["text"])} for e in state["evidence"]]
        prompt = "Alert: " + alert_text + "\n\nEvidence:\n" + "\n".join(
            f"[{e['id']}] ({e['source']}) {e['text']}" for e in evidence)
        return {"alert_text": alert_text, "evidence": evidence, "prompt": prompt,
                "redactions": redactor.record()}

    async def explain(state: State) -> State:
        try:
            written = await writer.write(SYSTEM, state["prompt"])
            return {"text": written.text, "model": written.model}
        except Exception as exc:  # noqa: BLE001 — the note still goes out, built from the evidence
            log.warning("the model could not write the note: %s: %s", type(exc).__name__, exc)
            listing = "\n".join(f"[{e['id']}] ({e['source']}) {e['text']}" for e in state["evidence"])
            return {"text": "The model could not write this note; the evidence follows.\n" + listing,
                    "model": "none", "error": f"{type(exc).__name__}: {exc}"}

    async def publish(state: State) -> State:
        alert = state["alert"]
        header = (f"Generated by the infrastructure agent with {state['model']}. "
                  "Advice beside the alert, not a diagnosis.")
        note = {
            "alertname": alert.get("labels", {}).get("alertname", "unnamed"),
            "severity": alert.get("labels", {}).get("severity", "unknown"),
            "fingerprint": alert.get("fingerprint", ""),
            "starts_at": alert.get("startsAt", ""),
            "alert": state["alert_text"],
            "text": f"{header}\n\n{state['text']}",
            "generated": True,
            "model": state["model"],
            "evidence": [e["id"] + " " + e["source"] for e in state["evidence"]],
            # The record of the sending: what was redacted, by category, never the values.
            "redactions": state["redactions"],
            "error": state.get("error", ""),
        }
        await publisher.publish(note)
        return {}

    graph = StateGraph(State)
    for name, step in (("gather", gather), ("correlate", correlate), ("redact", redact),
                       ("explain", explain), ("publish", publish)):
        graph.add_node(name, step)
    graph.add_edge(START, "gather")
    graph.add_edge("gather", "correlate")
    graph.add_edge("correlate", "redact")
    graph.add_edge("redact", "explain")
    graph.add_edge("explain", "publish")
    graph.add_edge("publish", END)
    return graph.compile()
