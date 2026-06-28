# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for request/response tenant-scoping (ADR-0019)."""
import os
import sys
import uuid

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import policy  # noqa: E402
import scoping  # noqa: E402

CID = str(uuid.uuid4())
PREFIX = policy.tenant_prefix(CID)


def test_request_name_is_qualified():
    out = scoping.scope_request("experiments/create", {"name": "churn", "artifact_location": "x"}, CID)
    assert out["name"] == PREFIX + "churn"
    assert out["artifact_location"] == "x"  # untouched


def test_request_without_name_field_unchanged():
    out = scoping.scope_request("runs/log-metric", {"run_id": "r1", "key": "auc", "value": 0.9}, CID)
    assert out == {"run_id": "r1", "key": "auc", "value": 0.9}


def test_response_scalar_nested_name_stripped():
    resp = {"experiment": {"experiment_id": "5", "name": PREFIX + "churn"}}
    out = scoping.scope_response("experiments/get", resp, CID)
    assert out["experiment"]["name"] == "churn"


def test_response_list_strips_and_drops_foreign():
    other = policy.tenant_prefix(str(uuid.uuid4()))
    resp = {"experiments": [
        {"experiment_id": "1", "name": PREFIX + "mine"},
        {"experiment_id": "2", "name": other + "theirs"},      # another tenant — must be dropped
        {"experiment_id": "3", "name": PREFIX + "also-mine"},
    ]}
    out = scoping.scope_response("experiments/search", resp, CID)
    names = [e["name"] for e in out["experiments"]]
    assert names == ["mine", "also-mine"]  # foreign entry dropped, prefixes stripped


def test_response_registered_models_list():
    resp = {"registered_models": [{"name": PREFIX + "m1"}, {"name": PREFIX + "m2"}]}
    out = scoping.scope_response("registered-models/search", resp, CID)
    assert [m["name"] for m in out["registered_models"]] == ["m1", "m2"]


def test_round_trip_create_then_get():
    req = scoping.scope_request("experiments/create", {"name": "exp"}, CID)
    # MLflow would store req["name"]; a later get returns it, which we strip back to plain.
    resp = scoping.scope_response("experiments/get-by-name", {"experiment": {"name": req["name"]}}, CID)
    assert resp["experiment"]["name"] == "exp"


def test_registered_model_description_passes_through_untouched():
    # The model `description` is user-facing free text, not a namespaced name — scoping must
    # qualify only the name and leave the description verbatim on the way in and out, so a tenant
    # can read back exactly what they wrote (the frontend Models page surfaces/edits it).
    desc = "VPD anomaly detector — retrained 2026-06."
    req = scoping.scope_request("registered-models/create", {"name": "vpd", "description": desc}, CID)
    assert req["name"] == PREFIX + "vpd"
    assert req["description"] == desc  # untouched

    resp = scoping.scope_response(
        "registered-models/get",
        {"registered_model": {"name": req["name"], "description": desc}},
        CID,
    )
    assert resp["registered_model"]["name"] == "vpd"   # stripped to plain
    assert resp["registered_model"]["description"] == desc  # untouched
