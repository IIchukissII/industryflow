# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Unit tests for the experiment/run read-path namespace rules (ADR-0019). These cover the pure helpers
that enforce tenant isolation + name stripping, with no live MLflow — mirroring the registered-models
scoping tests. The MLflow REST round-trips are cluster-bound.

The regression these stand guard over is the one ADR-0019 records: the removed ``/api/mlflow/*``
shim returned *every* tenant's experiments. Any shaping path that fails to drop a foreign experiment
reintroduces it.
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

import mlflow_namespace as rm  # noqa: E402

CID = str(uuid.uuid4())
OTHER = str(uuid.uuid4())
PREFIX = rm.tenant_prefix(CID)


def _experiment(name, **over):
    base = {
        "name": name,
        "experiment_id": "7",
        "lifecycle_stage": "active",
        "creation_time": 1,
        "last_update_time": 2,
    }
    base.update(over)
    return base


# --------------------------------------------------------------------------- experiments

def test_shape_experiment_strips_tenant_prefix():
    out = rm.shape_experiment(_experiment(PREFIX + "vpd-tuning"), PREFIX)
    assert out["name"] == "vpd-tuning"  # prefix stripped — UI never sees tenant_<uuid>
    assert out["experiment_id"] == "7"
    assert out["lifecycle_stage"] == "active"
    assert out["source"] == "notebook"


def test_shape_experiment_drops_foreign_experiment():
    # The #114 leak, as a unit test: another tenant's experiment must never shape into a response.
    foreign = _experiment(rm.tenant_prefix(OTHER) + "theirs")
    assert rm.shape_experiment(foreign, PREFIX) is None


def test_shape_experiment_drops_unprefixed_experiment():
    # MLflow's own "Default" experiment is unprefixed and belongs to no tenant.
    assert rm.shape_experiment(_experiment("Default"), PREFIX) is None


def test_shape_experiment_drops_prefix_lookalike():
    # A tenant whose token is a string-prefix of another's must not match. Guards the boundary the
    # trailing '.' exists to enforce.
    token = rm.tenant_token(CID)
    assert rm.shape_experiment(_experiment(token + "_evil.theirs"), PREFIX) is None


# --------------------------------------------------------------------------- runs

def test_shape_run_carries_metrics_and_params():
    out = rm.shape_run({
        "run_id": "r1",
        "run_name": "sweep-3",
        "status": "FINISHED",
        "start_time": 10,
        "end_time": 20,
        "metrics": {"rmse": 0.4},
        "params": {"depth": "6"},
    })
    assert out["run_id"] == "r1"
    assert out["run_name"] == "sweep-3"
    assert out["status"] == "FINISHED"
    assert out["metrics"] == {"rmse": 0.4}
    assert out["params"] == {"depth": "6"}


def test_shape_run_defaults_missing_fields():
    out = rm.shape_run({"run_id": "r2"})
    assert out["run_name"] == ""
    assert out["metrics"] == {}
    assert out["params"] == {}


# --------------------------------------------------------------------------- name safety

def test_is_safe_name_accepts_plain_names():
    assert rm.is_safe_name("vpd-tuning")
    assert rm.is_safe_name("sweep_2026.01")


def test_is_safe_name_rejects_filter_breaking_values():
    # These would otherwise land inside an MLflow search filter string.
    assert not rm.is_safe_name("")
    assert not rm.is_safe_name("a'b")
    assert not rm.is_safe_name('a"b')
    assert not rm.is_safe_name("a%b")
