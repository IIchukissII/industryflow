# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The tenant-naming contract (ADR-0003 dec 4, 5): every name is built from a validated UUID only."""
import uuid

import pytest

from industryflow_contracts import tenancy

CID = "550e8400-e29b-41d4-a716-446655440000"
TOKEN = "tenant_550e8400_e29b_41d4_a716_446655440000"

BUILDERS = [
    tenancy.tenant_token,
    tenancy.schema_name,
    tenancy.reader_role_name,
    tenancy.sql_login_name,
    tenancy.mlflow_name_prefix,
    tenancy.mlflow_artifact_prefix,
    tenancy.cold_object_prefix,
]


def test_every_name_the_platform_derives_from_a_tenant():
    # These are the names already in databases, registries and buckets: changing one orphans data.
    assert tenancy.tenant_token(CID) == TOKEN
    assert tenancy.schema_name(CID) == TOKEN
    assert tenancy.reader_role_name(CID) == "tenant_reader_550e8400_e29b_41d4_a716_446655440000"
    assert tenancy.sql_login_name(CID) == "tenant_sqlproxy_550e8400_e29b_41d4_a716_446655440000"
    assert tenancy.mlflow_name_prefix(CID) == TOKEN + "."
    assert tenancy.mlflow_artifact_prefix(CID) == TOKEN + "/"
    assert tenancy.cold_object_prefix(CID) == "tenant_550e8400-e29b-41d4-a716-446655440000"


def test_the_two_spellings_are_distinct():
    """The identifier form never carries a hyphen; the cold-archive prefix keeps the canonical ones."""
    assert "-" not in tenancy.schema_name(CID)
    assert tenancy.cold_object_prefix(CID) == "tenant_" + CID
    assert tenancy.cold_object_prefix(CID) != tenancy.schema_name(CID)


@pytest.mark.parametrize("spelling", [
    CID.upper(),
    "{" + CID + "}",
    "urn:uuid:" + CID,
    CID.replace("-", ""),
    uuid.UUID(CID),
])
def test_any_spelling_of_one_tenant_yields_one_name(spelling):
    assert tenancy.canonical_company_id(spelling) == CID
    for build in BUILDERS:
        assert build(spelling) == build(CID)


@pytest.mark.parametrize("bad", [
    'x"; DROP SCHEMA public; --',
    "550e8400-e29b-41d4-a716-44665544000",   # one digit short
    "../" + CID,
    "",
    None,
    "tenant_" + CID,
])
@pytest.mark.parametrize("build", BUILDERS + [tenancy.canonical_company_id])
def test_a_value_that_is_not_a_uuid_never_becomes_a_name(build, bad):
    with pytest.raises(ValueError):
        build(bad)


def test_a_schema_name_reads_back_to_its_tenant():
    assert tenancy.company_id_from_schema(TOKEN) == CID
    for _ in range(20):
        cid = str(uuid.uuid4())
        assert tenancy.company_id_from_schema(tenancy.schema_name(cid)) == cid


@pytest.mark.parametrize("name", [
    "public",
    "tenant_",
    "tenant_registry",
    "tenant_reader_550e8400_e29b_41d4_a716_446655440000",
    "tenant_550E8400_E29B_41D4_A716_446655440000",        # not what schema_name builds
    "tenant_550e8400e29b41d4a716446655440000",              # nor this
    "tenant_550e8400-e29b-41d4-a716-446655440000",          # the cold prefix is not a schema
    "tenant_550e8400_e29b_41d4_a716_446655440000; DROP",
    None,
])
def test_only_a_name_the_mapping_builds_is_taken_for_a_tenant_schema(name):
    with pytest.raises(ValueError):
        tenancy.company_id_from_schema(name)


def test_the_catalog_pattern_selects_tenant_schemas_literally():
    # `_` is a LIKE wildcard; escaped, the pattern cannot also match e.g. `tenantX...`.
    assert tenancy.SCHEMA_LIKE == r"tenant\_%"
