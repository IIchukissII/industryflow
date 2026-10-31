# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Redaction before anything leaves for a model (ADR-0045 dec 6, ADR-0046 dec 8).

The categories are the ones ADR-0046 names: tenant identifiers, user addresses, network addresses and
credential-shaped strings. Each value becomes a stable placeholder within one note (the same tenant
is `<tenant-1>` wherever it appears), so the evidence stays correlated after redaction. The record of
a sending keeps how many values of each category were replaced, never the values.

Identifiers are bounded by non-identifier characters rather than word boundaries, because the
platform writes them inside names joined by underscores (`tenant_<uuid>`, `cold_read_<…>`), where a
word boundary never falls. Network addresses are confirmed by the standard library's parser, so
compressed IPv6 is caught and a clock time is not.
"""

from __future__ import annotations

import ipaddress
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

_HEX = "0-9a-fA-F"
_UUID = rf"[{_HEX}]{{8}}[-_][{_HEX}]{{4}}[-_][{_HEX}]{{4}}[-_][{_HEX}]{{4}}[-_][{_HEX}]{{12}}"
# A key that names a credential, anywhere in an identifier: password, POSTGRES_PASSWORD, access_token,
# client_secret, aws_secret_access_key, api-key.
_KEY = r"[A-Za-z0-9_.-]*(?:passw(?:or)?d|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key)[A-Za-z0-9_.-]*"


def _is_ip(text: str) -> bool:
    candidate = text.strip("[]").split("%", 1)[0]
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return False
    return True


# (category, pattern, accept). Order matters: credentials are claimed before the identifiers inside
# them, and the value group `v`, when a pattern has one, is the only part replaced, so the key stays
# readable. A tenant's schema and role names carry its uuid, which the uuid pattern replaces in
# place; the names themselves are the contract package's, not restated here.
_PATTERNS: list[tuple[str, re.Pattern[str], Callable[[str], bool] | None]] = [
    ("secret", re.compile(r"(?i)\b(?:bearer|basic)\s+(?P<v>[A-Za-z0-9._~+/=-]{8,})"), None),
    ("secret", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), None),  # JWT
    ("secret", re.compile(r"\b(?:sk|ghp|gho|ghu|ghs|ghr|github_pat|xox[abpr])[-_][A-Za-z0-9_-]{16,}"), None),
    ("secret", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), None),
    # scheme://user:password@host — the password only.
    ("secret", re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^/\s:@]*:(?P<v>[^@\s/]+)@"), None),
    # "key": "value" and key=value / key: value, quoted or not.
    ("secret", re.compile(rf"(?i)\"{_KEY}\"\s*:\s*\"(?P<v>[^\"]+)\""), None),
    ("secret", re.compile(rf"(?i)(?<![A-Za-z0-9]){_KEY}\s*[=:]\s*[\"']?(?P<v>[^\s,;&\"'<>]{{2,}})"), None),
    ("tenant", re.compile(rf"(?<![{_HEX}]){_UUID}(?![{_HEX}])"), None),
    ("tenant", re.compile(rf"(?<![{_HEX}])[{_HEX}]{{32}}(?![{_HEX}])"), None),
    ("email", re.compile(r"[A-Za-z0-9._%+-]+(?:@|%40)[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), None),
    ("address", re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])"), _is_ip),
    ("address", re.compile(rf"(?<![\w:.])\[?[{_HEX}:]*:[{_HEX}:]*(?:%\w+)?\]?(?![\w:.])"), _is_ip),
    ("secret", re.compile(rf"(?<![{_HEX}])[{_HEX}]{{40,}}(?![{_HEX}])"), None),
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

    def _replace(self, category: str, accept: Callable[[str], bool] | None, m: re.Match[str]) -> str:
        whole = m.group(0)
        if accept is not None and not accept(whole):
            return whole
        if "v" not in m.re.groupindex:
            return self._placeholder(category, whole.strip())
        start, end = m.span("v")
        offset = m.start()
        return whole[: start - offset] + self._placeholder(category, m.group("v")) + whole[end - offset:]

    def __call__(self, text: str) -> str:
        for category, pattern, accept in _PATTERNS:
            text = pattern.sub(lambda m, c=category, a=accept: self._replace(c, a, m), text)
        return text

    def record(self) -> dict[str, int]:
        """What a sending's record keeps: counts per category, never a value."""
        return dict(sorted(self.counts.items()))

    def summary(self) -> str:
        """The record as one line, for the dashboards."""
        return ", ".join(f"{k} {v}" for k, v in self.record().items()) or "nothing"
