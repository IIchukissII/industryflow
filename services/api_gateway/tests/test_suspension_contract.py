# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Every plane that holds a copy of the capability-store contract honours the store's denials.

The gateway writes the suspension marker when a company is suspended (ADR-0033 dec 3), and the store's
rebuild record once its denials are re-derived (ADR-0015 dec 8). A verifier or minter that kept its own
copy of the store's key format but not the shared check would keep honouring, or issuing, a suspended
company's handles, or trust a store that lost its markers, and nothing would fail. So the set of copies
is found by searching for the store's key prefix, not listed by hand; none may spell the marker itself
(its one home is ``industryflow_contracts.denials``), and each verifier or minter consults the shared
check.
"""
import os
import re

import capability_auth
from industryflow_contracts import denials

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
SERVICES = os.path.join(ROOT, "services")


def _copies():
    """Every non-test source in services/ that declares the capability store's key prefix."""
    found = {}
    for dirpath, dirnames, filenames in os.walk(SERVICES):
        dirnames[:] = [d for d in dirnames if d not in ("tests", "node_modules", "__pycache__")]
        for name in filenames:
            if name.endswith(".py"):
                path = os.path.join(dirpath, name)
                src = open(path, encoding="utf-8").read()
                if re.search(r'^_KEY_PREFIX = "nbcap:"$', src, re.M):
                    found[os.path.relpath(path, ROOT)] = src
    return found


def test_the_copies_are_found():
    # The search itself is the guard; if it stops finding the known copies it guards nothing.
    assert {
        "services/api_gateway/capability_auth.py",
        "services/notebook_hub/capabilities.py",
        "services/notebook_sql_proxy/binding.py",
        "services/notebook_tracking_gateway/policy.py",
        "services/notebook_cold_broker/policy.py",
        "services/ml_service/api/upload_capability.py",
    } <= set(_copies())


# Copies of the store's key format that neither resolve nor mint, and why. Every other copy found
# must consult the shared check.
NOT_VERIFIERS = {
    # Builds the upload minter's records; the route that writes them consults the check (below).
    "services/ml_service/api/upload_capability.py",
    # Purges a deleted tenant and marks it; it grants nothing.
    "services/tenant_delete/capability_store.py",
}


def test_no_copy_spells_the_marker_itself():
    for path, src in _copies().items():
        assert denials.SUSPENDED_PREFIX not in src, f"{path} spells the suspension marker instead of denials"


def test_every_verifier_and_minter_consults_the_shared_check():
    copies = _copies()
    assert NOT_VERIFIERS <= set(copies), "a named non-verifier no longer holds the key format"
    for path, src in copies.items():
        if path in NOT_VERIFIERS:
            continue
        assert re.search(r"denials\.tenant_refusal(_sync)?\(", src), \
            f"{path} holds the capability store's key format without the shared denial check"


def test_the_ml_route_that_mints_consults_the_shared_check():
    route = open(os.path.join(SERVICES, "ml_service", "api", "routers", "models.py"), encoding="utf-8").read()
    assert "denials.tenant_refusal(" in route


def test_the_upload_index_is_the_one_the_gateway_revokes():
    # The minter writes each upload handle into a per-user index; ending the user's sessions revokes
    # by that same index. If the two copies of its name drift, revocation silently misses them.
    minter = open(os.path.join(SERVICES, "ml_service", "api", "upload_capability.py"), encoding="utf-8").read()
    route = open(os.path.join(SERVICES, "ml_service", "api", "routers", "models.py"), encoding="utf-8").read()
    assert f'_UPLOAD_INDEX_PREFIX = "{capability_auth._UPLOAD_INDEX_PREFIX}"' in minter
    assert "upload_capability.user_index_key(" in route
