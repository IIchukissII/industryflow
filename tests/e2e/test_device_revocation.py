# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A revoked device is refused by the deployed edge (ADR-0007 dec 6, dec 8; ADR-0002): admitted, then
revoked at the device CA (scripts/device-ca.sh revoke, which re-signs the list), then refused in the
handshake once the edge has re-read the list, while another device of the same tenant is still admitted.

The edge reads its list only when it loads its configuration; its watcher reloads it when the list
changes (services/ingestion_edge/40-crl-reload.sh), every CRL_RELOAD_INTERVAL_SECONDS. Probes carry a
body the schema refuses, so an admitted device is answered 422 and nothing reaches the stream."""
import httpx

from harness import eventually, issue_device, send_reading

EDGE = "industryflow-ingestion-edge"


def _reload_interval(env) -> int:
    """The running edge's watcher interval, as the watcher reads it."""
    return int(env.run("docker", "exec", EDGE, "sh", "-c", 'echo "${CRL_RELOAD_INTERVAL_SECONDS:-60}"').strip())


def _admitted(settings, cert) -> bool:
    """True if the edge admits the certificate (the body is then refused, 422); False if it refuses it."""
    try:
        r = send_reading(settings, cert, {})
    except httpx.ConnectError as exc:
        # Refused in the handshake: a TLS alert, not an edge that is down or unreachable.
        assert "SSL" in repr(exc) or "certificate" in str(exc).lower(), repr(exc)
        return False
    if r.status_code in (400, 403, 495, 496):
        return False
    assert r.status_code == 422, r.text[:300]
    return True


def test_a_revoked_device_is_refused_and_its_neighbour_is_not(env, settings, tenant, device):
    revoked = issue_device(env, settings["IF_E2E_COMPOSE_DIR"], tenant["company_id"], f"e2e-revoked-{tenant['run']}")
    assert _admitted(settings, revoked), "a freshly issued certificate is refused"
    assert _admitted(settings, device)

    env.script("scripts/device-ca.sh", "revoke", tenant["company_id"], f"e2e-revoked-{tenant['run']}")

    interval = _reload_interval(env)
    eventually(lambda: not _admitted(settings, revoked), timeout=3 * interval + 30, every=5,
               what="the edge refuses the revoked device once it has re-read its list")
    assert _admitted(settings, device), "revoking one device refused another of the same tenant"
