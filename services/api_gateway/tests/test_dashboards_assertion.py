# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The dashboards' sign-on assertion (ADR-0016 dec 7).

The assertion is verified the way the dashboards verify it: with the public half of the key, the
expected issuer and audience, and the expiry. The superuser gate is the shared dependency
(ADR-0043 dec 1), driven here through the real app so the route cannot lose it unnoticed.
"""
import os
import uuid
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException
from fastapi.testclient import TestClient
from jose import jwt

from routers import dashboards_assertion as da


def _keypair(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.PEM,
                                           serialization.PublicFormat.SubjectPublicKeyInfo)
    path = tmp_path / "signing.pem"
    path.write_bytes(private)
    return path, public.decode()


def _superuser():
    return SimpleNamespace(id=uuid.uuid4(), email="ops@example.test", is_superuser=True)


@pytest.mark.asyncio
async def test_assertion_verifies_as_the_dashboards_verify_it(tmp_path, monkeypatch):
    path, public = _keypair(tmp_path)
    monkeypatch.setenv("DASHBOARDS_SIGNING_KEY_FILE", str(path))

    resp = await da.dashboards_assertion(user=_superuser())

    assert resp.status_code == 204
    assert resp.headers["Cache-Control"] == "no-store"
    claims = jwt.decode(resp.headers[da.ASSERTION_HEADER], public, algorithms=["ES256"],
                        audience=da.AUDIENCE, issuer=da.ISSUER)
    assert claims["sub"] == claims["email"] == "ops@example.test"
    assert claims["role"] == "Editor"
    assert claims["exp"] - claims["iat"] == da.LIFETIME_SECONDS


@pytest.mark.asyncio
async def test_assertion_does_not_verify_under_another_key(tmp_path, monkeypatch):
    path, _ = _keypair(tmp_path)
    _, other_public = _keypair(tmp_path / "other")
    monkeypatch.setenv("DASHBOARDS_SIGNING_KEY_FILE", str(path))

    token = (await da.dashboards_assertion(user=_superuser())).headers[da.ASSERTION_HEADER]

    with pytest.raises(jwt.JWTError):
        jwt.decode(token, other_public, algorithms=["ES256"], audience=da.AUDIENCE, issuer=da.ISSUER)


@pytest.mark.asyncio
async def test_a_replaced_key_signs_without_a_restart(tmp_path, monkeypatch):
    path, old_public = _keypair(tmp_path)
    monkeypatch.setenv("DASHBOARDS_SIGNING_KEY_FILE", str(path))
    await da.dashboards_assertion(user=_superuser())

    _, new_public = _keypair(tmp_path)  # overwrites the same file, as rotation does
    os.utime(path, (1, 1))  # a distinct mtime even within the filesystem's resolution
    token = (await da.dashboards_assertion(user=_superuser())).headers[da.ASSERTION_HEADER]

    jwt.decode(token, new_public, algorithms=["ES256"], audience=da.AUDIENCE, issuer=da.ISSUER)
    with pytest.raises(jwt.JWTError):
        jwt.decode(token, old_public, algorithms=["ES256"], audience=da.AUDIENCE, issuer=da.ISSUER)


@pytest.mark.asyncio
async def test_without_a_key_the_sign_on_is_unavailable(monkeypatch):
    monkeypatch.delenv("DASHBOARDS_SIGNING_KEY_FILE", raising=False)
    with pytest.raises(HTTPException) as err:
        await da.dashboards_assertion(user=_superuser())
    assert err.value.status_code == 503


def test_the_route_admits_a_superuser_session_only(tmp_path, monkeypatch):
    import main
    from users import current_active_user

    path, public = _keypair(tmp_path)
    monkeypatch.setenv("DASHBOARDS_SIGNING_KEY_FILE", str(path))
    client = TestClient(main.app)
    assert client.get(da.PATH).status_code == 401

    tenant_admin = SimpleNamespace(id=uuid.uuid4(), email="admin@t.test", is_superuser=False,
                                   role="admin", company_id=uuid.uuid4())
    for user, expected in ((tenant_admin, 403), (_superuser(), 204)):
        main.app.dependency_overrides[current_active_user] = lambda u=user: u
        try:
            resp = client.get(da.PATH)
        finally:
            main.app.dependency_overrides.clear()
        assert resp.status_code == expected
    jwt.decode(resp.headers[da.ASSERTION_HEADER], public, algorithms=["ES256"],
               audience=da.AUDIENCE, issuer=da.ISSUER)


def test_the_path_is_outside_every_prefix_the_edge_routes():
    # The edge forwards /api/, /auth/, /users/ and a few exact paths (services/frontend/
    # nginx.conf.template); the config guard checks the edge side of this.
    assert da.PATH.startswith("/internal/")
