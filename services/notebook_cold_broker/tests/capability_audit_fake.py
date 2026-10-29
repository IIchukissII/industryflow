# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The capability-use record (ADR-0042) on the tests' dict-backed Redis fakes.

The fakes interpret the contract's two scripts by name and refuse any other, so a plane that starts
evaluating a script nobody modelled fails here rather than passing on an invented answer. What the
scripts really do is proven by running them in a Lua interpreter
(libs/industryflow_contracts/tests/test_capability_audit.py); this mirrors those semantics. A copy of
services/api_gateway/tests/capability_audit_fake.py: each service's suite runs on its own.
"""
from industryflow_contracts import capability_audit as audit


class AuditScripts:
    """Mixin for a fake with an async ``delete(*keys) -> int``; appended events land in ``stream``."""

    def _audit_state(self):
        if not hasattr(self, "stream"):
            self.stream, self.markers = [], set()

    async def eval(self, script, numkeys, *keys_and_args):
        self._audit_state()
        keys, args = list(keys_and_args[:numkeys]), list(keys_and_args[numkeys:])
        if script == audit.DELETE_RECORDED_SCRIPT:
            removed = await self.delete(keys[0])
            if removed == 1:
                self.stream.append(_pairs(args[1:]))
            return removed
        if script == audit.FIRST_USE_SCRIPT:
            if keys[0] in self.markers:
                return 0
            self.markers.add(keys[0])
            self.stream.append(_pairs(args[2:]))
            return 1
        raise AssertionError("a script the capability-audit fake does not model")

    async def xadd(self, name, fields, maxlen=None, approximate=True):
        assert name == audit.STREAM_KEY and maxlen == audit.STREAM_MAXLEN
        self._audit_state()
        self.stream.append(dict(fields))
        return f"{len(self.stream)}-0"

    def events(self, kind=None):
        self._audit_state()
        return [audit.decode(e) for e in self.stream if kind is None or e.get("kind") == kind]


def _pairs(flat):
    return dict(zip(flat[0::2], flat[1::2]))
