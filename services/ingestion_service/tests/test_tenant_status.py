# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A suspended tenant's devices are refused at ingestion, from the tenant-status stream (ADR-0033 dec 3).

The stream sheds load; the writers enforce. So a tenant the stream holds as suspended is refused, and
everything the stream cannot vouch for — a tenant never heard of, a status gone stale because the
publisher stopped, a malformed message — is let through rather than halting ingestion.
"""
import asyncio
import json
import uuid
from urllib.parse import quote

import pytest

from industryflow_contracts import tenant_status as contract

import tenant_status


def _headers(pem):
    return {"X-Client-Verify": "SUCCESS", "X-Client-Cert": quote(pem)}


def _heard(company_id, active):
    tenant_status.status.apply(contract.encode(company_id, active)[1])


def test_a_suspended_tenants_device_is_refused(client, make_cert, sample_body):
    c, produced = client
    company_id = str(uuid.uuid4())
    pem = make_cert(f"industryflow:tenant/{company_id}/device/press-1")
    _heard(company_id, False)
    r = c.post("/ingest", json=sample_body, headers=_headers(pem))
    assert r.status_code == 403 and r.json()["detail"] == "Tenant suspended"
    assert produced == []


def test_reactivation_lets_the_device_in_again(client, make_cert, sample_body):
    c, produced = client
    company_id = str(uuid.uuid4())
    pem = make_cert(f"industryflow:tenant/{company_id}/device/press-1")
    _heard(company_id, False)
    _heard(company_id, True)
    assert c.post("/ingest", json=sample_body, headers=_headers(pem)).status_code == 202
    assert len(produced) == 1


def test_a_tenant_never_heard_of_is_let_through(client, make_cert, sample_body):
    c, produced = client
    pem = make_cert(f"industryflow:tenant/{uuid.uuid4()}/device/press-1")
    assert c.post("/ingest", json=sample_body, headers=_headers(pem)).status_code == 202


def test_a_stale_suspension_is_forgotten():
    now = [0.0]
    status = tenant_status.TenantStatus(stale_after=60, clock=lambda: now[0])
    company_id = str(uuid.uuid4())
    status.apply(contract.encode(company_id, False)[1])
    now[0] = 59
    assert status.is_suspended(company_id)
    now[0] = 61
    assert not status.is_suspended(company_id)


def test_a_message_that_is_not_a_status_changes_nothing():
    status = tenant_status.TenantStatus()
    company_id = str(uuid.uuid4())
    status.apply(contract.encode(company_id, False)[1])
    status.apply(json.dumps({"company_id": company_id, "active": "true"}).encode())
    status.apply(b"garbage")
    assert status.is_suspended(company_id)


def test_the_follower_applies_the_stream_and_reconnects_after_a_failure(monkeypatch):
    company_id = str(uuid.uuid4())
    monkeypatch.setattr(tenant_status, "status", tenant_status.TenantStatus())
    started = []

    class Message:
        def __init__(self, value):
            self.value = value

    class Consumer:
        def __init__(self, fail):
            self.fail = fail

        async def start(self):
            started.append(self.fail)
            if self.fail:
                raise ConnectionError("broker unreachable")

        async def stop(self):
            pass

        def __aiter__(self):
            return self._messages()

        async def _messages(self):
            yield Message(contract.encode(company_id, False)[1])
            raise asyncio.CancelledError

    consumers = iter([Consumer(True), Consumer(False)])
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(tenant_status.follow(lambda: next(consumers), retry_seconds=0))
    assert started == [True, False]
    assert tenant_status.status.is_suspended(company_id)
