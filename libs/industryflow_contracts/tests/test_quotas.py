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
    got = quotas.defaults(env)
    assert (got["readings_per_second"], got["notebook_environments"]) == (1200, None)
    assert set(got) == set(quotas.CAPS)


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


def test_the_sql_caps_are_caps_with_finite_shipped_defaults():
    for cap in (quotas.SQL_SESSIONS, quotas.SQL_STATEMENT_SECONDS, quotas.SQL_IDLE_TRANSACTION_SECONDS):
        assert cap in quotas.CAPS
        assert quotas.defaults({})[cap] == quotas.SHIPPED_DEFAULTS[cap] > 0
    assert quotas.defaults({"TENANT_QUOTA_SQL_STATEMENT_SECONDS": "900"})["sql_statement_seconds"] == 900


def test_the_total_of_sql_sessions_is_finite_and_never_off():
    assert quotas.sql_sessions_total({}) == quotas.SQL_SESSIONS_TOTAL_SHIPPED
    assert quotas.sql_sessions_total({"NOTEBOOK_SQL_SESSIONS_TOTAL": " 80 "}) == 80
    # A tenant's shipped cap fits the shipped total many times over, so one tenant cannot take it.
    assert quotas.SHIPPED_DEFAULTS[quotas.SQL_SESSIONS] * 2 <= quotas.SQL_SESSIONS_TOTAL_SHIPPED
    for raw in ("off", "0", "-1", "many"):
        with pytest.raises(ValueError, match="NOTEBOOK_SQL_SESSIONS_TOTAL"):
            quotas.sql_sessions_total({"NOTEBOOK_SQL_SESSIONS_TOTAL": raw})


def test_the_sql_session_sets_name_the_canonical_tenant():
    assert quotas.sql_sessions_key(CID.upper()) == f"quota:sql-sessions:{CID}"



def test_a_tenant_is_over_a_stored_cap_only_on_a_fresh_measurement_past_it():
    G = quotas.GIB
    caps = {quotas.STORED_DATABASE_GIB: 10, quotas.STORED_OBJECT_GIB: 20}
    usage = {"database": (10 * G + 1, 1000.0), "object": (20 * G, 1000.0)}
    assert quotas.stored_over(usage, caps, now=1100, stale_after=3600) == {quotas.STORED_DATABASE_GIB}
    # A measurement older than the stale bound holds nothing (dec 8); nor does a cap that is off.
    assert quotas.stored_over(usage, caps, now=1000 + 3601, stale_after=3600) == frozenset()
    assert quotas.stored_over(usage, {**caps, quotas.STORED_DATABASE_GIB: None}, 1100, 3600) == frozenset()
    assert quotas.stored_over({}, caps, 1100, 3600) == frozenset()


def test_the_stored_caps_and_their_staleness_are_configured():
    assert set(quotas.STORED_CAPS.values()) <= set(quotas.CAPS)
    assert quotas.usage_stale_seconds({}) == quotas.USAGE_STALE_SHIPPED
    assert quotas.usage_stale_seconds({"TENANT_USAGE_STALE_SECONDS": "600"}) == 600
    with pytest.raises(ValueError, match="TENANT_USAGE_STALE_SECONDS"):
        quotas.usage_stale_seconds({"TENANT_USAGE_STALE_SECONDS": "soon"})
    assert quotas.over_key(quotas.STORED_OBJECT_GIB, CID.upper()) == f"quota:over:stored_object_gib:{CID}"


DERIVED_ENV = {"INGEST_CAPACITY_READINGS_PER_SECOND": "500", "NOTEBOOK_SESSION_CAPACITY": "4"}


def test_where_the_capacity_is_derived_the_shipped_default_is_half_of_it():
    """ADR-0049 dec 6, 14: a share of the derived capacity; elsewhere the amounts hold."""
    held = quotas.settings(DERIVED_ENV)
    assert held["readings_per_second"] == held["notebook_environments"] == quotas.Share(50)
    assert held["sql_sessions"] == quotas.SHIPPED_DEFAULTS["sql_sessions"]
    got = quotas.defaults(DERIVED_ENV)
    assert (got["readings_per_second"], got["notebook_environments"]) == (250, 2)
    assert quotas.capacities(DERIVED_ENV) == {"readings_per_second": 500, "notebook_environments": 4}


def test_a_share_is_configured_and_resolved_against_what_the_overrides_leave():
    env = {**DERIVED_ENV, "TENANT_QUOTA_READINGS_PER_SECOND": "20%"}
    share = quotas.settings(env)["readings_per_second"]
    assert share == quotas.Share(20) and str(share) == "20%"
    assert quotas.defaults(env)["readings_per_second"] == 100
    assert quotas.resolve(share, 500, reserved=300) == 40
    # Never below one: a tenant is never refused everything.
    assert quotas.resolve(share, 500, reserved=500) == 1 == quotas.resolve(share, 500, reserved=900)
    assert quotas.resolve(250, 500, reserved=500) == 250 and quotas.resolve(None, 500) is None


def test_each_tenant_takes_its_override_else_its_share_of_the_rest():
    caps = quotas.effective_caps(quotas.Share(50), 500, {"a": 300}, ["a", "b", "c"])
    assert caps == {"a": 300, "b": 100, "c": 100}
    assert quotas.effective_caps(250, None, {"a": 40}, ["a", "b"]) == {"a": 40, "b": 250}


def test_overrides_past_the_capacity_are_overcommitted():
    assert quotas.overcommitted(500, {"a": 300, "b": 201})
    assert not quotas.overcommitted(500, {"a": 300, "b": 200})
    assert not quotas.overcommitted(None, {"a": 10 ** 6})


@pytest.mark.parametrize("raw", ["0%", "101%", "x%", "50%%", "1.5%"])
def test_a_mistyped_share_is_refused_naming_its_setting(raw):
    with pytest.raises(ValueError, match="TENANT_QUOTA_READINGS_PER_SECOND"):
        quotas.parse_setting(quotas.READINGS_PER_SECOND, raw, capacity=500)


def test_a_share_without_a_derived_capacity_is_refused():
    """Where nothing derives the capacity (a cluster) a share has nothing to be a share of: an amount holds."""
    with pytest.raises(ValueError, match="derives no capacity"):
        quotas.settings({"TENANT_QUOTA_NOTEBOOK_ENVIRONMENTS": "50%"})
    with pytest.raises(ValueError, match="derives no capacity"):
        quotas.parse_setting(quotas.SQL_SESSIONS, "50%", capacity=10)
    with pytest.raises(ValueError, match="NOTEBOOK_SESSION_CAPACITY"):
        quotas.capacities({"NOTEBOOK_SESSION_CAPACITY": "many"})
