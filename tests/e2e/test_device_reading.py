# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A device's reading through the certificate edge to the tenant's data (ADR-0026 dec 10): a device
certificate issued for the tenant by the device CA, a reading sent through the mutual-TLS edge with
it, the tenant taken from the certificate and never from the body, the reading read back through the
API after the stream has written it (ADR-0002, ADR-0007, ADR-0005, ADR-0006)."""
import httpx
import pytest

from harness import equipment, eventually, reading, send_reading


@pytest.fixture(scope="module")
def sensor(tenant):
    eid, ids = equipment(tenant["engineer"], "cabinet", f"e2e-cabinet-{tenant['run']}", ["temp_root"])
    return eid, ids["temp_root"]


def test_a_devices_reading_reaches_its_tenants_data(settings, tenant, sensor, device):
    eid, sid = sensor
    value = 21.25
    r = send_reading(settings, device, reading(eid, sid, value))
    assert r.status_code == 202, r.text[:300]
    assert r.json()["company_id"] == tenant["company_id"]  # from the certificate (ADR-0002 dec 3)

    def written():
        rows = tenant["engineer"].get("/api/measurements/latest").json()
        return [m for m in rows if str(m.get("sensor_id")) == sid and abs(float(m["value"]) - value) < 1e-9]

    eventually(written, timeout=240, every=10, what="the reading is written by the stream and read back")


def test_the_edge_refuses_a_reading_without_a_device_certificate(settings, sensor):
    eid, sid = sensor
    try:
        r = send_reading(settings, None, reading(eid, sid, 1.0))
    except httpx.ConnectError as exc:
        # Refused in the handshake: a TLS alert, not an edge that is down or unreachable.
        assert "SSL" in repr(exc) or "certificate" in str(exc).lower(), repr(exc)
        return
    assert r.status_code in (400, 401, 403, 495, 496), r.text[:200]


def test_a_body_naming_another_tenant_is_not_believed(settings, tenant, sensor, device):
    eid, sid = sensor
    body = {**reading(eid, sid, 2.0), "company_id": "00000000-0000-4000-8000-000000000000"}
    r = send_reading(settings, device, body)
    # The schema refuses a tenant in the body, or the certificate's tenant wins; never the body's.
    assert r.status_code in (202, 422), r.text[:300]
    if r.status_code == 202:
        assert r.json()["company_id"] == tenant["company_id"]
