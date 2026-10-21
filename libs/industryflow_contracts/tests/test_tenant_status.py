# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The tenant-status message round-trips, and anything else is refused (ADR-0033 dec 3)."""
import json

import pytest

from industryflow_contracts import tenant_status

CID = "0b2f503a-6e59-4f4f-b0cd-b82547a20cf2"


@pytest.mark.parametrize("active", [True, False])
def test_a_status_round_trips_under_the_canonical_id(active):
    key, value = tenant_status.encode(CID.upper(), active)
    assert key == CID.encode()
    assert tenant_status.decode(value) == (CID, active)


@pytest.mark.parametrize("value", [
    b"not json",
    b"[]",
    json.dumps({"company_id": CID}).encode(),
    json.dumps({"company_id": CID, "active": "false"}).encode(),  # a string is not a status
    json.dumps({"company_id": "tenant_x", "active": False}).encode(),
    json.dumps({"active": False}).encode(),
])
def test_anything_else_is_refused(value):
    with pytest.raises(ValueError):
        tenant_status.decode(value)


def test_encode_refuses_a_non_uuid_and_a_non_bool():
    with pytest.raises(ValueError):
        tenant_status.encode("not-a-uuid", True)
    with pytest.raises(ValueError):
        tenant_status.encode(CID, 1)


def test_a_consumer_trusts_several_intervals_before_forgetting():
    assert tenant_status.STALE_AFTER_SECONDS >= 3 * tenant_status.PUBLISH_INTERVAL_SECONDS
