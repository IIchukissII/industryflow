# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The runbooks' symptom, cause and fix tables, read as a contract (ADR-0046 dec 9).

A runbook table is a markdown table whose header is exactly `Symptom | Cause | Fix`. Rows are matched
to an alert by shared words, deterministically: the runbooks already hold structured rows, so no
similarity store is needed (ADR-0046 Alternatives, dec 9).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

HEADER = ("symptom", "cause", "fix")
_WORD = re.compile(r"[a-z0-9]+")
# Words that appear in nearly every row and alert, so they carry no signal.
_STOP = frozenset(
    "a an and are as at be by for from has in is it its no not of on or the to was were with "
    "industryflow platform alert".split()
)


@dataclass(frozen=True)
class Row:
    symptom: str
    cause: str
    fix: str
    source: str


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse(text: str, source: str) -> list[Row]:
    """Every row of every `Symptom | Cause | Fix` table in one markdown document."""
    rows: list[Row] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].lstrip().startswith("|") and tuple(c.lower() for c in _cells(lines[i])) == HEADER:
            i += 2  # the header and its separator
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                cells = _cells(lines[i])
                if len(cells) == 3:
                    rows.append(Row(*cells, source=source))
                i += 1
            continue
        i += 1
    return rows


def load(directory: Path) -> list[Row]:
    rows: list[Row] = []
    for path in sorted(directory.rglob("*.md")):
        rows.extend(parse(path.read_text(encoding="utf-8"), path.name))
    return rows


def _words(text: str) -> set[str]:
    # CamelCase alert names split into their words: IndustryFlowRedisMemoryHigh -> redis, memory, high.
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)
    return {w for w in _WORD.findall(spaced.lower()) if w not in _STOP and len(w) > 2}


def match(rows: list[Row], text: str, limit: int = 3) -> list[Row]:
    """The rows sharing the most words with the alert, best first; none when nothing is shared."""
    wanted = _words(text)
    scored = [(len(wanted & _words(f"{r.symptom} {r.cause}")), n, r) for n, r in enumerate(rows)]
    scored = [s for s in scored if s[0] > 0]
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [r for _, _, r in scored[:limit]]
