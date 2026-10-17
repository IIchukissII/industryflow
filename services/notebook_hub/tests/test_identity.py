# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for the notebook hub identity + spawn-profile logic (ADR-0014, ADR-0011 dec 5)."""
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import identity as ident  # noqa: E402


def _headers(user="u-1", company_id=None, role="engineer"):
    company_id = company_id or str(uuid.uuid4())
    return {
        ident.HEADER_USER: user,
        ident.HEADER_COMPANY_ID: company_id,
        ident.HEADER_ROLE: role,
    }


def test_parse_identity_canonicalises_tenant():
    cid = uuid.uuid4()
    got = ident.parse_identity(_headers(company_id=str(cid), role="operator"))
    assert got.user == "u-1"
    assert got.company_id == str(cid)
    assert got.role == "operator"


def test_parse_identity_is_case_insensitive():
    cid = str(uuid.uuid4())
    headers = {"x-if-user": "u-9", "x-if-company-id": cid, "x-if-role": "admin"}
    got = ident.parse_identity(headers)
    assert got.user == "u-9" and got.company_id == cid and got.role == "admin"


@pytest.mark.parametrize(
    "headers",
    [
        {ident.HEADER_USER: "", ident.HEADER_COMPANY_ID: str(uuid.uuid4()), ident.HEADER_ROLE: "admin"},
        {ident.HEADER_USER: "u", ident.HEADER_COMPANY_ID: str(uuid.uuid4()), ident.HEADER_ROLE: ""},
        {ident.HEADER_USER: "u", ident.HEADER_COMPANY_ID: "not-a-uuid", ident.HEADER_ROLE: "admin"},
        {ident.HEADER_USER: "u", ident.HEADER_COMPANY_ID: "", ident.HEADER_ROLE: "admin"},
    ],
)
def test_parse_identity_rejects_bad_input(headers):
    with pytest.raises(ValueError):
        ident.parse_identity(headers)


@pytest.mark.parametrize("role", ["admin", "engineer"])
def test_authoring_roles_get_authoring_profile(role):
    assert ident.select_profile(role, ident.DEFAULT_AUTHORING_ROLES) == ident.PROFILE_AUTHORING


@pytest.mark.parametrize("role", ["observer", "data_scientist", "", "unknown"])
def test_other_roles_get_readonly_analytics(role):
    assert ident.select_profile(role, ident.DEFAULT_AUTHORING_ROLES) == ident.PROFILE_ANALYTICS


def test_default_authoring_roles_are_platform_roles():
    assert ident.DEFAULT_AUTHORING_ROLES == {"admin", "engineer"}
    assert ident.DEFAULT_AUTHORING_ROLES <= ident.PLATFORM_ROLES


def test_authoring_roles_unset_is_the_default():
    assert ident.parse_authoring_roles(None) == ident.DEFAULT_AUTHORING_ROLES


@pytest.mark.parametrize(
    "value, expected",
    [
        ("admin", {"admin"}),
        ("admin, engineer ,observer", {"admin", "engineer", "observer"}),
        ("engineer,engineer,", {"engineer"}),
    ],
)
def test_authoring_roles_are_configurable(value, expected):
    roles = ident.parse_authoring_roles(value)
    assert roles == expected
    assert ident.select_profile("observer", roles) == (
        ident.PROFILE_AUTHORING if "observer" in expected else ident.PROFILE_ANALYTICS
    )


@pytest.mark.parametrize("value", ["", " , ", "admin,data_scientist", "Admin", "operator"])
def test_authoring_roles_fail_closed_on_empty_or_unknown(value):
    with pytest.raises(ValueError, match=ident.AUTHORING_ROLES_ENV):
        ident.parse_authoring_roles(value)


def test_pod_labels_bind_tenant_and_profile():
    cid = str(uuid.uuid4())
    got = ident.pod_labels(ident.Identity(user="u", company_id=cid, role="observer"), ident.PROFILE_ANALYTICS)
    assert got["industryflow.io/company-id"] == cid
    assert got["industryflow.io/profile"] == ident.PROFILE_ANALYTICS
    assert got["app.kubernetes.io/name"] == "notebook-singleuser"


def test_pod_environment_carries_identity_not_credentials():
    cid = str(uuid.uuid4())
    env = ident.pod_environment(
        ident.Identity(user="alice", company_id=cid, role="engineer"), ident.PROFILE_AUTHORING
    )
    assert env == {
        "INDUSTRYFLOW_USER": "alice",
        "INDUSTRYFLOW_COMPANY_ID": cid,
        "INDUSTRYFLOW_PROFILE": ident.PROFILE_AUTHORING,
    }
    # No data credential is ever placed in the environment (ADR-0012 dec 5).
    joined = " ".join(k.lower() for k in env).replace("_", "")
    assert "password" not in joined and "token" not in joined and "secret" not in joined


def test_image_for_profile_uses_per_profile_env():
    env = {
        ident.AUTHORING_IMAGE_ENV: "if-authoring:1",
        ident.ANALYTICS_IMAGE_ENV: "if-analytics:1",
    }
    assert ident.image_for_profile(ident.PROFILE_AUTHORING, env) == "if-authoring:1"
    assert ident.image_for_profile(ident.PROFILE_ANALYTICS, env) == "if-analytics:1"


def test_image_for_profile_falls_back_to_single_image():
    # A one-image dev setup (legacy NOTEBOOK_IMAGE) serves both profiles.
    env = {"NOTEBOOK_IMAGE": "if-notebook:dev"}
    assert ident.image_for_profile(ident.PROFILE_AUTHORING, env) == "if-notebook:dev"
    assert ident.image_for_profile(ident.PROFILE_ANALYTICS, env) == "if-notebook:dev"


def test_image_for_profile_requires_some_image():
    with pytest.raises(KeyError):
        ident.image_for_profile(ident.PROFILE_AUTHORING, {})


# --- re-checking an established hub login against the identity forwarded now (ADR-0014) ---------

_CID = str(uuid.uuid4())
_STATE = {"company_id": _CID, "role": "engineer"}


def test_reconcile_same_identity():
    assert ident.reconcile(_headers(user="u-1", company_id=_CID), "u-1", _STATE) == ident.RECONCILE_SAME


def test_reconcile_another_user_behind_the_cookie():
    verdict = ident.reconcile(_headers(user="u-2", company_id=_CID), "u-1", _STATE)
    assert verdict == ident.RECONCILE_OTHER_USER


@pytest.mark.parametrize("over", [{"company_id": str(uuid.uuid4())}, {"role": "operator"}])
def test_reconcile_same_user_with_other_tenant_or_role(over):
    headers = _headers(user="u-1", **{"company_id": _CID, **over})
    assert ident.reconcile(headers, "u-1", _STATE) == ident.RECONCILE_STALE_FACTS


def test_reconcile_without_a_forwarded_identity_is_not_a_mismatch():
    # A single-user server's own API call reaches the hub directly, with no identity headers.
    assert ident.reconcile({"Authorization": "token x"}, "u-1", _STATE) == ident.RECONCILE_NOT_FORWARDED


@pytest.mark.parametrize("headers", [
    {ident.HEADER_USER: "u-1"},                                                    # partial
    {ident.HEADER_USER: "u-1", ident.HEADER_COMPANY_ID: "nope", ident.HEADER_ROLE: "engineer"},
])
def test_reconcile_unparseable_identity_is_invalid(headers):
    assert ident.reconcile(headers, "u-1", _STATE) == ident.RECONCILE_INVALID


# --- the SSO proxy's secret (ADR-0014 dec 3) ------------------------------------------------------

_SECRET = "f" * 64


def test_from_proxy_accepts_the_shared_secret_under_any_header_casing():
    assert ident.from_proxy({ident.HEADER_PROXY_SECRET: _SECRET}, _SECRET)
    assert ident.from_proxy({ident.HEADER_PROXY_SECRET.lower(): _SECRET}, _SECRET)


@pytest.mark.parametrize("headers", [
    {},
    {ident.HEADER_PROXY_SECRET: ""},
    {ident.HEADER_PROXY_SECRET: _SECRET[:-1]},
    {ident.HEADER_PROXY_SECRET: _SECRET + "f"},
    {ident.HEADER_PROXY_SECRET: _SECRET.upper()},
    {ident.HEADER_PROXY_SECRET: "é" * 64},
    {"X-IF-SSO-Proxy": _SECRET},
])
def test_from_proxy_refuses_anything_else(headers):
    assert not ident.from_proxy(headers, _SECRET)


@pytest.mark.parametrize("secret", ["", None])
def test_from_proxy_with_no_expected_secret_matches_nothing(secret):
    assert not ident.from_proxy({ident.HEADER_PROXY_SECRET: ""}, secret)
    assert not ident.from_proxy({ident.HEADER_PROXY_SECRET: "x"}, secret)


def test_validate_proxy_secret_accepts_a_generated_secret():
    assert ident.validate_proxy_secret(" " + _SECRET + "\n") == _SECRET


@pytest.mark.parametrize("value", [None, "", "   ", "CHANGE_ME_openssl_rand_hex_32",
                                   "changeme" + "a" * 40, "a" * 31, "a" * 31 + "$", "a b" * 20])
def test_validate_proxy_secret_refuses_unset_placeholder_or_unfit(value):
    with pytest.raises(ValueError, match=ident.PROXY_SECRET_ENV):
        ident.validate_proxy_secret(value)
