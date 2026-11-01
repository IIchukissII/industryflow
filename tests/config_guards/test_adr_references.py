# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Every cross-reference to a decision resolves (ADR-0000 dec 10).

Code, configuration and documents cite decisions as `ADR-NNNN dec N`, and decision numbers are never
renumbered (ADR-0000 dec 8) precisely so those citations stay true. A citation fails silently when an
ADR is compacted or a decision moves to another record: the comment keeps pointing at a decision that
does not say what it claims. This guard reads both sides: the numbered decisions each ADR holds, and
every citation and relative Markdown link in the tracked files.
"""

from __future__ import annotations

import re
import subprocess

from _repo import ROOT

TEXT_SUFFIXES = {".md", ".py", ".sh", ".yml", ".yaml", ".sql", ".ts", ".tsx", ".js", ".conf",
                 ".toml", ".ini", ".j2", ".tpl", ".txt", ".example", ".env", ".dockerfile"}
CITE = re.compile(
    r"ADR-(\d{4})\)?,? (?:decisions?|decs?\.?|§) ?"
    r"(\d+(?:\s*(?:,|and|-|–)\s*\d+)*)")
ADR_ID = re.compile(r"\bADR-(\d{4})\b")
LINK = re.compile(r"\]\(([^)\s#]+)(?:#[^)]*)?\)")
DECISION_ITEM = re.compile(r"^(\d+)\.\s", re.M)


def _tracked_text_files():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True,
                         capture_output=True, text=True).stdout
    for rel in filter(None, out.split("\0")):
        path = ROOT / rel
        if (path.suffix in TEXT_SUFFIXES or path.name.startswith("Dockerfile")) and path.is_file():
            yield rel, path.read_text(errors="replace")


def _decisions():
    held = {}
    for path in (ROOT / "ADR").glob("ADR-[0-9][0-9][0-9][0-9]-*.md"):
        text = path.read_text()
        section = text.split("\n## Decision\n", 1)[1].split("\n## ", 1)[0]
        held[path.name[4:8]] = {int(n) for n in DECISION_ITEM.findall(section)}
    return held


FILES = dict(_tracked_text_files())
DECISIONS = _decisions()


def _line(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _cited_numbers(spec: str) -> set[int]:
    numbers = set()
    for part in re.split(r"\s*(?:,|and)\s*", spec):
        bounds = [int(n) for n in re.split(r"\s*[-–]\s*", part)]
        numbers.update(range(bounds[0], bounds[-1] + 1))
    return numbers


def test_the_guard_is_looking():
    assert len(DECISIONS) > 40 and all(DECISIONS.values()), "ADR decisions not parsed"
    assert sum(len(CITE.findall(t)) for t in FILES.values()) > 500, "citations not found"


def test_every_cited_adr_exists():
    missing = [f"{rel}:{_line(text, m.start())}: ADR-{m.group(1)}"
               for rel, text in FILES.items() for m in ADR_ID.finditer(text)
               if m.group(1) not in DECISIONS]
    assert not missing, "cites an ADR that does not exist:\n" + "\n".join(missing)


def test_every_cited_decision_exists():
    dangling = []
    for rel, text in FILES.items():
        for m in CITE.finditer(text):
            adr = m.group(1)
            for n in sorted(_cited_numbers(m.group(2)) - DECISIONS.get(adr, set())):
                dangling.append(f"{rel}:{_line(text, m.start())}: ADR-{adr} dec {n}"
                                f" (it holds 1-{max(DECISIONS.get(adr, {0}))})")
    assert not dangling, "cites a decision the ADR does not hold:\n" + "\n".join(dangling)


def test_every_relative_markdown_link_resolves():
    broken = []
    for rel, text in FILES.items():
        if not rel.endswith(".md"):
            continue
        base = (ROOT / rel).parent
        for m in LINK.finditer(text):
            target = m.group(1)
            if re.match(r"[a-z][a-z0-9+.-]*:", target) or target.startswith("<"):
                continue
            if not (base / target).exists():
                broken.append(f"{rel}:{_line(text, m.start())}: {target}")
    assert not broken, "links to a file that does not exist:\n" + "\n".join(broken)
