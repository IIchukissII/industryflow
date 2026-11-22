# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A tenant's readings cap (ADR-0049): refused before production, across the tenant's devices, from
the override on the stream or the platform default, and admitted when the count cannot be read."""
import uuid
from urllib.parse import quote

import pytest

from industryflow_contracts import quotas, tenant_status as contract


def _headers(make_cert, tenant, device="gw-01"):
    pem = make_cert(f"industryflow:tenant/{tenant}/device/{device}")
    return {"x-client-verify": "SUCCESS", "x-client-cert": quote(pem)}


@pytest.fixture
def default_cap(monkeypatch):
    import config

    def set_default(value):
        monkeypatch.setattr(config.get_settings(), "READINGS_PER_SECOND_DEFAULT", value)

    return set_default


@pytest.fixture
def frozen_second(monkeypatch):
    """Every reading of the test falls in one second of the clock."""
    import quota

    monkeypatch.setattr(quota.time, "time", lambda: 1_800_000_000.25)


def test_readings_over_the_cap_are_refused_across_the_tenants_devices_and_never_produced(
        client, make_cert, sample_body, default_cap, frozen_second):
    c, produced = client
    default_cap(3)
    tenant, other = str(uuid.uuid4()), str(uuid.uuid4())
    answers = [c.post("/ingest", json=sample_body, headers=_headers(make_cert, tenant, f"gw-{i}")).status_code
               for i in range(5)]
    assert answers == [202, 202, 202, 429, 429]
    assert len(produced) == 3
    refused = c.post("/ingest", json=sample_body, headers=_headers(make_cert, tenant))
    assert refused.headers["retry-after"] == "1" and "3 readings per second" in refused.json()["detail"]
    # Another tenant has its own count.
    assert c.post("/ingest", json=sample_body, headers=_headers(make_cert, other)).status_code == 202


def test_the_override_heard_on_the_stream_replaces_the_default(client, make_cert, sample_body, default_cap,
                                                              frozen_second):
    import tenant_status

    c, produced = client
    default_cap(1)
    tenant = str(uuid.uuid4())
    _key, value = contract.encode(tenant, True, {quotas.READINGS_PER_SECOND: 4})
    tenant_status.status.apply(value)
    answers = [c.post("/ingest", json=sample_body, headers=_headers(make_cert, tenant)).status_code for _ in range(5)]
    assert answers == [202] * 4 + [429]


def test_an_off_cap_admits_everything(client, make_cert, sample_body, default_cap, frozen_second):
    c, produced = client
    default_cap(None)
    tenant = str(uuid.uuid4())
    assert {c.post("/ingest", json=sample_body, headers=_headers(make_cert, tenant)).status_code
            for _ in range(20)} == {202}


def test_an_unreachable_count_admits_the_reading_and_counts_the_failure(client, make_cert, sample_body,
                                                                       default_cap, monkeypatch):
    import main
    import quota

    class Down:
        def pipeline(self, transaction=True):
            raise ConnectionError("store unreachable")

        async def aclose(self):
            return None

    c, produced = client
    default_cap(1)
    monkeypatch.setattr(main, "quota_store", Down())
    before = quota.COUNT_UNAVAILABLE.labels(cap="readings_per_second")._value.get()
    tenant = str(uuid.uuid4())
    assert [c.post("/ingest", json=sample_body, headers=_headers(make_cert, tenant)).status_code
            for _ in range(3)] == [202, 202, 202]
    assert quota.COUNT_UNAVAILABLE.labels(cap="readings_per_second")._value.get() == before + 3


def test_a_refusal_is_recorded_against_the_tenant_and_in_the_tenantless_total(
        client, make_cert, sample_body, default_cap, frozen_second):
    import main

    c, _produced = client
    default_cap(1)
    tenant = str(uuid.uuid4())
    for _ in range(3):
        c.post("/ingest", json=sample_body, headers=_headers(make_cert, tenant))

    with main.quota_store.sync() as store:
        day = store.hgetall(quotas.refusals_key("readings_per_second", "20270115"))
        last = store.hget(quotas.refused_last_key("readings_per_second"), tenant)
        ttl = store.ttl(quotas.refusals_key("readings_per_second", "20270115"))
        total = store.get(quotas.refusals_total_key("readings_per_second"))
    assert int(total) == 2
    assert {k.decode(): int(v) for k, v in day.items()} == {tenant: 2}
    assert int(last) == 1_800_000_000
    assert 0 < ttl <= quotas.REFUSALS_KEEP_DAYS * 86400


def test_the_count_keys_expire(client, make_cert, sample_body, default_cap, frozen_second):
    import main

    c, _ = client
    default_cap(5)
    tenant = str(uuid.uuid4())
    c.post("/ingest", json=sample_body, headers=_headers(make_cert, tenant))
    with main.quota_store.sync() as store:
        ttl = store.ttl(quotas.readings_key(tenant, 1_800_000_000))
    assert 0 < ttl <= quotas.READINGS_KEY_TTL_SECONDS
