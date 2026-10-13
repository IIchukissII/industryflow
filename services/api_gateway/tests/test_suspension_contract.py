# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Every plane that holds a copy of the capability-store contract honours the suspension marker.

The gateway writes the marker when a company is suspended (ADR-0033 dec 3). A verifier or minter
that kept its own copy of the store's key format but not the marker would keep honouring, or
issuing, a suspended company's handles, and nothing would fail. So the set of copies is found by
searching for the store's key prefix, not listed by hand, and each one must carry the gateway's
marker prefix and consult it.
"""
import os
import re

import capability_auth

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


def test_every_copy_carries_the_marker_and_consults_it():
    marker = capability_auth.SUSPENDED_PREFIX
    for path, src in _copies().items():
        assert f'"{marker}"' in src, f"{path} does not know the suspension marker {marker!r}"
        uses = len(re.findall(r"_SUSPENDED_PREFIX|suspended_key\(", src))
        assert uses >= 2, f"{path} declares the suspension marker but never consults it"


def test_the_ml_route_that_mints_consults_the_marker():
    # upload_capability.py holds the rule; the route that writes the handle is where it is applied.
    route = open(os.path.join(SERVICES, "ml_service", "api", "routers", "models.py"), encoding="utf-8").read()
    assert "upload_capability.suspended_key(" in route


def test_the_upload_index_is_the_one_the_gateway_revokes():
    # The minter writes each upload handle into a per-user index; ending the user's sessions revokes
    # by that same index. If the two copies of its name drift, revocation silently misses them.
    minter = open(os.path.join(SERVICES, "ml_service", "api", "upload_capability.py"), encoding="utf-8").read()
    route = open(os.path.join(SERVICES, "ml_service", "api", "routers", "models.py"), encoding="utf-8").read()
    assert f'_UPLOAD_INDEX_PREFIX = "{capability_auth._UPLOAD_INDEX_PREFIX}"' in minter
    assert "upload_capability.user_index_key(" in route
