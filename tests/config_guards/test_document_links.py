# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Every relative link and heading anchor between the repository's documents resolves (ADR-0000 dec 10).

A renamed or removed file, or a renamed heading, leaves a link pointing at nothing, and the page that
holds the link is not part of the change that broke it. Citations of decisions are checked against
the records in force outside the repository, not here.
"""

from __future__ import annotations

import re
import subprocess

from _repo import ROOT

LINK = re.compile(r"\]\(([^)\s#]+)(?:#[^)]*)?\)")
ANCHORED_LINK = re.compile(r"\]\(([^)\s#]*)#([^)\s]+)\)")
HEADING_LINE = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")


def _documents():
    out = subprocess.run(["git", "ls-files", "-z", "--", "*.md"], cwd=ROOT, check=True,
                         capture_output=True, text=True).stdout
    for rel in filter(None, out.split("\0")):
        if (ROOT / rel).is_file():
            yield rel, (ROOT / rel).read_text(errors="replace")


DOCUMENTS = dict(_documents())


def _line(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _external(target: str) -> bool:
    return bool(re.match(r"[a-z][a-z0-9+.-]*:", target)) or target.startswith("<")


def test_the_guard_is_looking():
    links = [m for text in DOCUMENTS.values() for m in LINK.finditer(text) if not _external(m.group(1))]
    assert len(DOCUMENTS) >= 5 and len(links) >= 5, "documents or relative links not found"


def test_every_relative_markdown_link_resolves():
    broken = []
    for rel, text in DOCUMENTS.items():
        base = (ROOT / rel).parent
        for m in LINK.finditer(text):
            target = m.group(1)
            if not _external(target) and not (base / target).exists():
                broken.append(f"{rel}:{_line(text, m.start())}: {target}")
    assert not broken, "links to a file that does not exist:\n" + "\n".join(broken)


def _anchors(text: str) -> set[str]:
    """The fragment ids a Markdown renderer gives the headings of a page, duplicates suffixed."""
    out, seen, fenced = set(), {}, False
    for line in text.splitlines():
        if line.startswith("```"):
            fenced = not fenced
        m = None if fenced else HEADING_LINE.match(line)
        if not m:
            continue
        slug = re.sub(r"[^\w\- ]", "", m.group(1).lower()).replace(" ", "-")
        n = seen.get(slug, 0)
        seen[slug] = n + 1
        out.add(slug if n == 0 else f"{slug}-{n}")
    return out


def _broken_anchors(rel: str, text: str, read) -> list[str]:
    broken = []
    for m in ANCHORED_LINK.finditer(text):
        target, fragment = m.group(1), m.group(2)
        if re.match(r"[a-z][a-z0-9+.-]*:", target) or (target and not target.endswith(".md")):
            continue
        path = ((ROOT / rel).parent / target).resolve() if target else ROOT / rel
        page = read(path)
        if page is not None and fragment.lower() not in _anchors(page):
            broken.append(f"{rel}:{_line(text, m.start())}: {target}#{fragment}")
    return broken


def _read(path):
    return path.read_text() if path.is_file() else None


def test_the_anchor_guard_sees_a_missing_heading():
    page = "# T\n\n## Running backups\n\n[a](#running-backups) [b](#no-such-heading)\n"
    assert _broken_anchors("docs/x.md", page, lambda p: page) == ["docs/x.md:5: #no-such-heading"]


def test_every_markdown_anchor_resolves():
    broken = [b for rel, text in DOCUMENTS.items() for b in _broken_anchors(rel, text, _read)]
    assert not broken, "links to a heading that does not exist:\n" + "\n".join(broken)
