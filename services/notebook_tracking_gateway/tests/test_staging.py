# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Unit tests for the staging key rules (ADR-0030). Pure, no I/O.

The property that carries the design: staged bytes are unadmitted, so they must rest somewhere **no
tenant can address** — while still being partitioned per tenant, because unaddressable is not the
same as unpartitioned.
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import policy  # noqa: E402
import staging  # noqa: E402

CID_A = str(uuid.uuid4())
CID_B = str(uuid.uuid4())
TOKEN_A = policy.tenant_token(CID_A)
TOKEN_B = policy.tenant_token(CID_B)


# --- where staged bytes rest -------------------------------------------------------------------

def test_staging_sits_outside_every_tenant_prefix():
    """The artifact plane rewrites any key to sit under the caller's `tenant_<uuid>/`. A key rooted
    elsewhere is therefore unreachable through it — which is the whole reason staging is not simply
    a folder inside the uploader's own prefix."""
    key = staging.staging_key(TOKEN_A, staging.new_upload_id(), "model.skops")
    assert not policy.owns_artifact_key(CID_A, key)
    assert not policy.owns_artifact_key(CID_B, key)
    assert not key.startswith(policy.artifact_prefix(CID_A))


def test_the_artifact_plane_cannot_be_talked_into_reaching_staging():
    # Whatever a caller asks for, scope_artifact_key roots it in their own prefix — so the staging
    # root is not addressable from that plane even by naming it exactly.
    scoped = policy.scope_artifact_key(CID_A, staging.STAGING_ROOT + TOKEN_A + "/x/MLmodel")
    assert scoped.startswith(policy.artifact_prefix(CID_A))
    assert not scoped.startswith(staging.STAGING_ROOT)


def test_staging_is_partitioned_by_tenant():
    uid = staging.new_upload_id()
    a = staging.staging_key(TOKEN_A, uid, "m")
    b = staging.staging_key(TOKEN_B, uid, "m")
    assert a != b                      # same upload id, different tenants, different keys
    assert TOKEN_B not in a and TOKEN_A not in b


def test_the_staging_root_is_not_a_valid_tenant_token():
    # So it can never collide with a tenant's own space.
    assert not staging.STAGING_ROOT.startswith("tenant_")


# --- admitted bytes land in the tenant's own prefix ---------------------------------------------

def test_admitted_keys_are_inside_the_tenants_prefix():
    uid = staging.new_upload_id()
    key = staging.admitted_key(policy.artifact_prefix(CID_A), uid, "model.skops")
    assert policy.owns_artifact_key(CID_A, key)
    assert not policy.owns_artifact_key(CID_B, key)


def test_admitted_paths_say_the_artifact_was_uploaded():
    # Provenance is a fact, not a decoration (ADR-0030 dec 8): an uploaded artifact does not sit
    # where a run's output would.
    uid = staging.new_upload_id()
    key = staging.admitted_key(policy.artifact_prefix(CID_A), uid, "m")
    assert staging.ADMITTED_ROOT in key


def test_admitted_uri_addresses_the_upload():
    uid = staging.new_upload_id()
    uri = staging.admitted_uri("mlflow", policy.artifact_prefix(CID_A), uid)
    assert uri == f"s3://mlflow/{TOKEN_A}/uploads/{uid}"


# --- upload ids ---------------------------------------------------------------------------------

def test_upload_ids_are_opaque_and_do_not_repeat():
    ids = {staging.new_upload_id() for _ in range(200)}
    assert len(ids) == 200
    assert all(staging.is_upload_id(i) for i in ids)


def test_an_upload_id_that_could_alter_a_key_is_not_an_upload_id():
    for bad in ("../../etc", "a/b", "", "x", "a" * 200, "has space", "a;b"):
        assert not staging.is_upload_id(bad), bad


# --- member paths: refuse, never sanitise -------------------------------------------------------

def test_ordinary_member_paths_are_kept():
    for good in ("MLmodel", "model.skops", "code/loader.py", "a/b/c.bin"):
        assert staging.safe_member_path(good) == good


def test_traversal_and_absolute_paths_are_refused():
    for bad in ("../escape", "a/../../b", "/etc/passwd", "\\windows", "a/./b", "a//b", ".."):
        assert staging.safe_member_path(bad) is None, bad


def test_control_characters_and_empties_are_refused():
    for bad in ("", "a\x00b", "a\nb", "a\x7fb", None, "x" * 513):
        assert staging.safe_member_path(bad) is None


def test_backslashes_are_refused_rather_than_translated():
    # One separator means one reading of the path.
    assert staging.safe_member_path("a\\b") is None


def test_a_refused_path_is_not_quietly_rewritten():
    # Sanitising would store the caller's bytes somewhere they did not ask for and call it success.
    assert staging.safe_member_path("../../../etc/shadow") is None
