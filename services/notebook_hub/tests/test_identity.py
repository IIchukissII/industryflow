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


@pytest.mark.parametrize("role", ["admin", "engineer", "data_scientist"])
def test_authoring_roles_get_authoring_profile(role):
    assert ident.select_profile(role) == ident.PROFILE_AUTHORING


@pytest.mark.parametrize("role", ["operator", "viewer", "", "unknown"])
def test_other_roles_get_readonly_analytics(role):
    assert ident.select_profile(role) == ident.PROFILE_ANALYTICS


def test_pod_labels_bind_tenant_and_profile():
    cid = str(uuid.uuid4())
    got = ident.pod_labels(ident.Identity(user="u", company_id=cid, role="operator"))
    assert got["industryflow.io/company-id"] == cid
    assert got["industryflow.io/profile"] == ident.PROFILE_ANALYTICS
    assert got["app.kubernetes.io/name"] == "notebook-singleuser"


def test_pod_environment_carries_identity_not_credentials():
    cid = str(uuid.uuid4())
    env = ident.pod_environment(ident.Identity(user="alice", company_id=cid, role="engineer"))
    assert env == {
        "INDUSTRYFLOW_USER": "alice",
        "INDUSTRYFLOW_COMPANY_ID": cid,
        "INDUSTRYFLOW_PROFILE": ident.PROFILE_AUTHORING,
    }
    # No data credential is ever placed in the environment (ADR-0012 dec 5).
    joined = " ".join(k.lower() for k in env).replace("_", "")
    assert "password" not in joined and "token" not in joined and "secret" not in joined
