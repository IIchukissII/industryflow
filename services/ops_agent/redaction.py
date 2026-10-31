# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Redaction before anything leaves for a model (ADR-0045 dec 6, ADR-0046 dec 8).

The categories are the ones ADR-0046 names: tenant identifiers, user addresses, network addresses and
credential-shaped strings. Each value becomes a stable placeholder within one note (the same tenant
is `<tenant-1>` wherever it appears), so the evidence stays correlated after redaction. The record of
a sending keeps how many values of each category were replaced, never the values.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

_UUID = r"[0-9a-fA-F]{8}[-_][0-9a-fA-F]{4}[-_][0-9a-fA-F]{4}[-_][0-9a-fA-F]{4}[-_][0-9a-fA-F]{12}"

# Order matters: a credential pattern must claim `token=<uuid>` before the tenant pattern claims the
# uuid inside it, and a schema name must be taken whole before its uuid is.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("secret", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")),
    ("secret", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),  # JWT
    ("secret", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")),
    # key=value and "key": "value": only the value (group `v`) is replaced, so the key stays readable.
    ("secret", re.compile(
        r"(?i)\b(?:password|passwd|pwd|secret|token|api[_-]?key)\s*[=:]\s*(?P<v>[^\s,;&\"'<]{4,})"
    )),
    ("secret", re.compile(
        r"(?i)\"(?:password|passwd|pwd|secret|token|api[_-]?key)\"\s*:\s*\"(?P<v>[^\"<]{4,})\""
    )),
    ("tenant", re.compile(rf"\btenant_(?:sqlproxy_|reader_)?{_UUID}\b")),
    ("tenant", re.compile(rf"\b{_UUID}\b")),
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("address", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("address", re.compile(r"\b(?:[0-9a-fA-F]{1,4}:){4,7}[0-9a-fA-F]{1,4}\b")),
    ("secret", re.compile(r"\b[A-Fa-f0-9]{40,}\b")),
]


@dataclass
class Redactor:
    """One per note: the placeholders are stable within it and mean nothing outside it."""

    _seen: dict[tuple[str, str], str] = field(default_factory=dict)
    counts: Counter = field(default_factory=Counter)

    def _placeholder(self, category: str, value: str) -> str:
        key = (category, value)
        if key not in self._seen:
            n = sum(1 for c, _ in self._seen if c == category) + 1
            self._seen[key] = f"<{category}-{n}>"
        self.counts[category] += 1
        return self._seen[key]

    def _replace(self, category: str, m: re.Match[str]) -> str:
        if "v" not in m.re.groupindex:
            return self._placeholder(category, m.group(0).strip())
        start, end = m.span("v")
        whole = m.group(0)
        offset = m.start()
        return whole[: start - offset] + self._placeholder(category, m.group("v")) + whole[end - offset:]

    def __call__(self, text: str) -> str:
        for category, pattern in _PATTERNS:
            text = pattern.sub(lambda m, c=category: self._replace(c, m), text)
        return text

    def record(self) -> dict[str, int]:
        """What a sending's record keeps: counts per category, never a value."""
        return dict(sorted(self.counts.items()))
