# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Unit tests for the registered-models read-path namespace rules (ADR-0019). These cover the pure
helpers that enforce tenant isolation + name stripping, with no live MLflow — mirroring the
notebook_tracking_gateway scoping tests. The MLflow REST round-trips are cluster-bound.
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

import mlflow_namespace as rm  # noqa: E402

CID = str(uuid.uuid4())
OTHER = str(uuid.uuid4())
PREFIX = rm.tenant_prefix(CID)


def test_tenant_prefix_is_dot_suffixed_uuid_token():
    # tenant_<uuid-with-underscores>.  — the dot is forced (MLflow forbids '/' ':' in model names).
    assert PREFIX == f"tenant_{CID.replace('-', '_')}."
    assert PREFIX.endswith(".")


def test_strip_owned_strips_own_prefix():
    assert rm.strip_owned(PREFIX, PREFIX + "vpd-regressor") == "vpd-regressor"


def test_strip_owned_rejects_foreign():
    other_prefix = rm.tenant_prefix(OTHER)
    assert rm.strip_owned(PREFIX, other_prefix + "secret") is None
    assert rm.strip_owned(PREFIX, "no-prefix-at-all") is None


def test_pick_latest_takes_highest_numeric_version():
    versions = [{"version": "1"}, {"version": "3"}, {"version": "2"}]
    assert rm.pick_latest(versions)["version"] == "3"


def test_pick_latest_empty_is_none():
    assert rm.pick_latest([]) is None


def test_shape_summary_strips_name_and_carries_metrics():
    model = {
        "name": PREFIX + "churn",
        "description": "monthly churn",
        "creation_timestamp": 1,
        "last_updated_timestamp": 2,
        "versions": [{"version": "1", "current_stage": "Production", "run_id": "r1"}],
    }
    out = rm.shape_summary(model, PREFIX, {"f1": 0.9})
    assert out["name"] == "churn"  # prefix stripped — UI never sees tenant_<uuid>
    assert out["description"] == "monthly churn"
    assert out["latest_version"] == "1"
    assert out["stage"] == "Production"
    assert out["metrics"] == {"f1": 0.9}
    assert out["source"] == "notebook"


def test_shape_summary_drops_foreign_model():
    foreign = {"name": rm.tenant_prefix(OTHER) + "theirs", "versions": []}
    assert rm.shape_summary(foreign, PREFIX, {}) is None
