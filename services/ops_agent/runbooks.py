# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The runbook rows the agent reads, its own data (ADR-0046 dec 9): `runbook.toml` beside this module.

Two kinds. An alert row exists for every platform rule and is looked up by the alert's exact name. A
symptom row (symptom, cause, fix) is matched to an alert by shared words, deterministically, and only when
at least two words are shared: a single shared word is noise, and no row is better than a wrong one. The
rows are already structured, so no similarity store is needed (ADR-0046 Alternatives, dec 9).
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

MIN_SHARED = 2
_WORD = re.compile(r"[a-z0-9]+")
# Words that appear in nearly every row and alert, so they carry no signal.
_STOP = frozenset(
    "a an and are as at be by for from has in is it its no not of on or the to was were with "
    "industryflow platform alert alerts severity critical warning firing since labels instance job "
    "has been last".split()
)


@dataclass(frozen=True)
class Row:
    symptom: str
    cause: str
    fix: str
    follows: str


@dataclass(frozen=True)
class AlertRow:
    alert: str
    fires_when: str
    handling: str
    follows: str


def _read(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def load_alerts(path: Path) -> dict[str, AlertRow]:
    """The alert rows by alert name."""
    return {a["name"]: AlertRow(a["name"], a["fires_when"], a["handling"], a["follows"])
            for a in _read(path).get("alert", [])}


def load(path: Path) -> list[Row]:
    """Every symptom row, in the file's order. Each names the decision it follows (`follows`)."""
    return [Row(r["symptom"], r["cause"], r["fix"], r["follows"]) for r in _read(path).get("symptom", [])]


def _words(text: str) -> set[str]:
    # CamelCase alert names split into their words: IndustryFlowRedisMemoryHigh -> redis, memory, high.
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)
    return {w for w in _WORD.findall(spaced.lower()) if w not in _STOP and len(w) > 2}


def match(rows: list[Row], text: str, limit: int = 3) -> list[Row]:
    """The rows sharing the most words with the alert, best first; only rows sharing MIN_SHARED words."""
    wanted = _words(text)
    scored = [(len(wanted & _words(f"{r.symptom} {r.cause}")), n, r) for n, r in enumerate(rows)]
    scored = [s for s in scored if s[0] >= MIN_SHARED]
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [r for _, _, r in scored[:limit]]
