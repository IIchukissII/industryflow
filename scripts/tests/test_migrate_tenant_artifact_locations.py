# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for the tenant artifact-location migration.

The shape under test is the one found on a deployed stack: an experiment the gateway created is
recorded at `mlflow-artifacts:/5`, its bytes are under `tenant_<uuid>/5/`, and a model version's
storage location points into `mlflow-artifacts:/5/models/...`, so serving's load 404s.
"""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from migrate_tenant_artifact_locations import plan, rewrite_statements  # noqa: E402

T = "tenant_0b2f503a_6e59_4f4f_b0cd_b82547a20cf2"


def _db():
    c = sqlite3.connect(":memory:")
    c.executescript("""
        CREATE TABLE experiments (experiment_id TEXT, name TEXT, artifact_location TEXT);
        CREATE TABLE runs (run_uuid TEXT, experiment_id TEXT, artifact_uri TEXT);
        CREATE TABLE logged_models (model_id TEXT, experiment_id TEXT, artifact_location TEXT);
        CREATE TABLE model_versions (name TEXT, source TEXT, storage_location TEXT);
    """)
    c.executemany("INSERT INTO experiments VALUES (?,?,?)", [
        ("5", f"{T}.vpd-model", "mlflow-artifacts:/5"),
        ("50", f"{T}.other", "mlflow-artifacts:/50"),
    ])
    c.executemany("INSERT INTO runs VALUES (?,?,?)", [
        ("r1", "5", "mlflow-artifacts:/5/r1/artifacts"),
        ("r50", "50", "mlflow-artifacts:/50/r50/artifacts"),
    ])
    c.execute("INSERT INTO logged_models VALUES ('m-1','5','mlflow-artifacts:/5/models/m-1/artifacts')")
    c.execute("INSERT INTO model_versions VALUES ('n','models:/m-1','mlflow-artifacts:/5/models/m-1/artifacts')")
    return c


def _apply(c, old, new):
    for sql, params in rewrite_statements(old, new):
        c.execute(sql, params)


def test_a_gateway_experiment_with_prefixed_bytes_is_migrated():
    migrate, skipped = plan([("5", f"{T}.vpd-model", "mlflow-artifacts:/5")], lambda p: 0)
    assert migrate == [("5", "mlflow-artifacts:/5", f"mlflow-artifacts:/{T}/5")]
    assert skipped == []


def test_records_under_the_old_root_move_and_a_lookalike_id_does_not():
    c = _db()
    _apply(c, "mlflow-artifacts:/5", f"mlflow-artifacts:/{T}/5")
    q = lambda sql: c.execute(sql).fetchall()  # noqa: E731
    assert q("SELECT artifact_location FROM experiments WHERE experiment_id='5'") == [(f"mlflow-artifacts:/{T}/5",)]
    assert q("SELECT artifact_uri FROM runs WHERE run_uuid='r1'") == [(f"mlflow-artifacts:/{T}/5/r1/artifacts",)]
    assert q("SELECT artifact_location FROM logged_models") == [(f"mlflow-artifacts:/{T}/5/models/m-1/artifacts",)]
    assert q("SELECT source, storage_location FROM model_versions") == [
        ("models:/m-1", f"mlflow-artifacts:/{T}/5/models/m-1/artifacts")]
    # Experiment 50 shares the string prefix "mlflow-artifacts:/5" and must not be touched.
    assert q("SELECT artifact_location FROM experiments WHERE experiment_id='50'") == [("mlflow-artifacts:/50",)]
    assert q("SELECT artifact_uri FROM runs WHERE run_uuid='r50'") == [("mlflow-artifacts:/50/r50/artifacts",)]


def test_unprefixed_objects_mean_the_records_are_right_and_are_left_alone():
    migrate, skipped = plan([("7", f"{T}.direct", "mlflow-artifacts:/7")], lambda p: 3 if p == "7/" else 0)
    assert migrate == []
    assert skipped and skipped[0][0] == "7"


def test_non_tenant_custom_and_already_migrated_experiments_are_ignored():
    rows = [
        ("0", "Default", "s3://mlflow/0"),
        ("1", f"{T}.legacy", "s3://mlflow/1"),
        ("5", f"{T}.vpd-model", f"mlflow-artifacts:/{T}/5"),
    ]
    assert plan(rows, lambda p: 0) == ([], [])


def test_rerunning_is_a_no_op():
    c = _db()
    _apply(c, "mlflow-artifacts:/5", f"mlflow-artifacts:/{T}/5")
    before = c.execute("SELECT * FROM runs ORDER BY run_uuid").fetchall()
    _apply(c, "mlflow-artifacts:/5", f"mlflow-artifacts:/{T}/5")
    assert c.execute("SELECT * FROM runs ORDER BY run_uuid").fetchall() == before
