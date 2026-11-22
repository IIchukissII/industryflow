# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A cap's default comes from the configuration, finite unless it says off (ADR-0049 dec 2, 6)."""
import pytest

from industryflow_contracts import quotas

CID = "0b2f503a-6e59-4f4f-b0cd-b82547a20cf2"


def test_an_unset_default_is_the_shipped_finite_one():
    assert quotas.defaults({}) == quotas.SHIPPED_DEFAULTS
    assert all(isinstance(v, int) and v > 0 for v in quotas.SHIPPED_DEFAULTS.values())
    assert quotas.parse_setting(quotas.READINGS_PER_SECOND, "  ") == quotas.SHIPPED_DEFAULTS["readings_per_second"]


def test_the_configuration_sets_a_number_or_turns_a_cap_off():
    env = {"TENANT_QUOTA_READINGS_PER_SECOND": "1200", "TENANT_QUOTA_NOTEBOOK_ENVIRONMENTS": "OFF"}
    assert quotas.defaults(env) == {"readings_per_second": 1200, "notebook_environments": None}


@pytest.mark.parametrize("raw", ["0", "-3", "1.5", "lots", str(quotas.MAX_VALUE + 1), "true"])
def test_a_mistyped_default_is_refused_naming_its_setting(raw):
    with pytest.raises(ValueError, match="TENANT_QUOTA_NOTEBOOK_ENVIRONMENTS"):
        quotas.parse_setting(quotas.NOTEBOOK_ENVIRONMENTS, raw)


@pytest.mark.parametrize("value", [0, -1, True, 1.0, "5", quotas.MAX_VALUE + 1, None])
def test_an_override_is_a_whole_number_in_range(value):
    with pytest.raises(ValueError):
        quotas.check_value(value)
    assert quotas.check_value(7) == 7


def test_an_override_wins_and_an_off_default_stays_off_without_one():
    assert quotas.effective(250, 40) == 40
    assert quotas.effective(250, None) == 250
    assert quotas.effective(None, None) is None


def test_the_store_keys_name_the_canonical_tenant_and_only_known_caps():
    assert quotas.readings_key(CID.upper(), 17) == f"quota:readings:{CID}:17"
    assert quotas.cap_key("notebook_environments", CID) == f"quota:cap:notebook_environments:{CID}"
    assert quotas.refused_last_key("readings_per_second", "20261122") == "quota:refused-last:readings_per_second:20261122"
    with pytest.raises(ValueError):
        quotas.refusals_key("disk", "20261122")
