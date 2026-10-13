# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The object-store principals infrastructure/minio/minio-init.sh creates, against a real MinIO.

Each service holds its own principal (ADR-0025 dec 11, ADR-0030 dec 2) instead of the root key. A
policy is only as good as what the store actually enforces, so this runs the init script against a
live server and then acts AS each principal: what its consumer needs must work, and its neighbours'
buckets — and, for the read principals, every write — must be refused.

Needs a MinIO and the `mc` client: MINIO_TEST_ENDPOINT, MINIO_TEST_ROOT_USER,
MINIO_TEST_ROOT_PASSWORD, MC (path to mc). Without them it skips — unless IF_REQUIRE_LIVE_STACK=1
(as CI sets), which turns the skip into a failure so the gate cannot pass by not running.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "infrastructure" / "minio" / "minio-init.sh"
ENDPOINT = os.environ.get("MINIO_TEST_ENDPOINT", "")
MC = os.environ.get("MC") or shutil.which("mc") or ""

if not (ENDPOINT and MC):
    if os.environ.get("IF_REQUIRE_LIVE_STACK") == "1":
        pytest.fail("IF_REQUIRE_LIVE_STACK=1 but no MinIO/mc configured", pytrace=False)
    pytest.skip("no live MinIO (MINIO_TEST_ENDPOINT / MC unset)", allow_module_level=True)

ROOT_USER = os.environ["MINIO_TEST_ROOT_USER"]
ROOT_PASSWORD = os.environ["MINIO_TEST_ROOT_PASSWORD"]

BUCKETS = {
    "SPARK_CHECKPOINT_BUCKET": "t-checkpoints",
    "ARTIFACT_BUCKET": "t-mlflow",
    "BACKUP_BUCKET": "t-backups",
    "COLD_STORE_BUCKET": "t-cold",
}
# principal name -> (env prefix, access key)
PRINCIPALS = {
    "spark-checkpoint": ("SPARK_CHECKPOINT", "t-spark"),
    "mlflow-artifacts": ("MLFLOW_STORE", "t-mlflow-server"),
    "tracking-gateway": ("TRACKING_STORE", "t-gateway"),
    "model-read": ("MODEL_READ", "t-serving"),
    "db-backup": ("BACKUP_STORE", "t-backup"),
    "cold-export-rw": ("COLD_STORE", "t-cold-writer"),
    "cold-broker-ro": ("COLD_READ", "t-cold-reader"),
}
CP, ART, BAK, COLD = (BUCKETS[k] for k in ("SPARK_CHECKPOINT_BUCKET", "ARTIFACT_BUCKET", "BACKUP_BUCKET", "COLD_STORE_BUCKET"))
UPLOADED = f"{ART}/tenant_aaaa/uploads/u1/model.skops"
TRACKED = f"{ART}/tenant_aaaa/7/abc/artifacts/model/model.skops"


def _env(home: Path, **overrides) -> dict:
    env = dict(os.environ, HOME=str(home), PATH=f"{Path(MC).parent}:{os.environ['PATH']}",
               MINIO_ENDPOINT=ENDPOINT, MINIO_ROOT_ACCESS_KEY=ROOT_USER, MINIO_ROOT_SECRET_KEY=ROOT_PASSWORD,
               UPLOAD_STAGING_PREFIX="_upload-staging/", UPLOAD_STAGING_EXPIRE_DAYS="1", **BUCKETS)
    for name, (prefix, access) in PRINCIPALS.items():
        env[f"{prefix}_ACCESS_KEY"] = access
        env[f"{prefix}_SECRET_KEY"] = f"{access}-secret-0123456789"
    env.update(overrides)
    return env


def _init(home: Path, **overrides) -> subprocess.CompletedProcess:
    return subprocess.run(["sh", str(SCRIPT)], env=_env(home, **overrides), capture_output=True, text=True)


@pytest.fixture(scope="module")
def home(tmp_path_factory):
    h = tmp_path_factory.mktemp("mc-home")
    first = _init(h)
    assert first.returncode == 0, first.stdout + first.stderr
    # Every `up` and every `helm upgrade` runs it again: it must converge, not fail or duplicate.
    again = _init(h)
    assert again.returncode == 0, again.stdout + again.stderr
    _mc(h, "alias", "set", "root", ENDPOINT, ROOT_USER, ROOT_PASSWORD)
    rules = _mc(h, "ilm", "rule", "ls", f"root/{ART}").stdout
    assert rules.count("_upload-staging/") == 1, rules
    seed = h / "seed.bin"
    seed.write_bytes(b"x" * 64)
    for key in (UPLOADED, TRACKED, f"{CP}/state/0", f"{BAK}/timescaledb/a.dump", f"{COLD}/tenant_aaaa/year=2026/p.parquet"):
        assert _mc(h, "cp", str(seed), f"root/{key}").returncode == 0
    for name, (_, access) in PRINCIPALS.items():
        assert _mc(h, "alias", "set", name, ENDPOINT, access, f"{access}-secret-0123456789").returncode == 0
    return h


def _mc(home: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run([MC, "--config-dir", str(home / ".mc"), "--quiet", *args],
                          capture_output=True, text=True, timeout=60)


def can(home, who, op, key) -> bool:
    if op == "get":
        r = _mc(home, "cat", f"{who}/{key}")
    elif op == "list":
        r = _mc(home, "ls", f"{who}/{key}")
    elif op == "put":
        r = _mc(home, "cp", str(home / "seed.bin"), f"{who}/{key}")
    elif op == "delete":
        r = _mc(home, "rm", f"{who}/{key}")
    else:
        raise ValueError(op)
    return r.returncode == 0


ALLOWED = {
    "spark-checkpoint": [("put", f"{CP}/state/1"), ("get", f"{CP}/state/0"), ("list", f"{CP}/state/"),
                         ("delete", f"{CP}/state/1")],
    "mlflow-artifacts": [("put", f"{ART}/tenant_bbbb/1/x"), ("get", TRACKED), ("delete", f"{ART}/tenant_bbbb/1/x")],
    "tracking-gateway": [("put", f"{ART}/_upload-staging/tenant_aaaa/u2/m"), ("get", UPLOADED),
                         ("delete", f"{ART}/_upload-staging/tenant_aaaa/u2/m")],
    "model-read": [("get", UPLOADED), ("list", f"{ART}/tenant_aaaa/uploads/u1/")],
    "db-backup": [("put", f"{BAK}/timescaledb/b.dump"), ("list", f"{BAK}/timescaledb/"),
                  ("get", f"{BAK}/timescaledb/a.dump"), ("delete", f"{BAK}/timescaledb/b.dump")],
    "cold-export-rw": [("put", f"{COLD}/tenant_aaaa/year=2026/q.parquet"), ("get", f"{COLD}/tenant_aaaa/year=2026/p.parquet"),
                       ("list", f"{COLD}/tenant_aaaa/")],
    "cold-broker-ro": [("get", f"{COLD}/tenant_aaaa/year=2026/p.parquet"), ("list", f"{COLD}/tenant_aaaa/")],
}

REFUSED = {
    # The checkpoint principal stays checkpoint-only (ADR-0025 dec 11).
    "spark-checkpoint": [("get", TRACKED), ("put", f"{COLD}/x"), ("get", f"{BAK}/timescaledb/a.dump")],
    "mlflow-artifacts": [("get", f"{COLD}/tenant_aaaa/year=2026/p.parquet"), ("put", f"{CP}/x"), ("list", f"{BAK}/")],
    "tracking-gateway": [("get", f"{BAK}/timescaledb/a.dump"), ("put", f"{COLD}/x")],
    # Serving reads admitted uploads and nothing else: not tracked runs, not other buckets, no write.
    "model-read": [("put", f"{ART}/tenant_aaaa/uploads/u1/evil"), ("delete", UPLOADED), ("get", TRACKED),
                   ("list", f"{ART}/"), ("get", f"{COLD}/tenant_aaaa/year=2026/p.parquet")],
    "db-backup": [("get", TRACKED), ("put", f"{ART}/x")],
    "cold-export-rw": [("delete", f"{COLD}/tenant_aaaa/year=2026/p.parquet"), ("get", TRACKED)],
    "cold-broker-ro": [("put", f"{COLD}/tenant_aaaa/x"), ("delete", f"{COLD}/tenant_aaaa/year=2026/p.parquet"),
                       ("get", TRACKED)],
}


@pytest.mark.parametrize("who", PRINCIPALS)
def test_each_principal_can_do_its_job(home, who):
    failed = [f"{op} {key}" for op, key in ALLOWED[who] if not can(home, who, op, key)]
    assert not failed, f"{who} was refused what its consumer needs: {failed}"


@pytest.mark.parametrize("who", PRINCIPALS)
def test_each_principal_is_refused_outside_its_scope(home, who):
    leaked = [f"{op} {key}" for op, key in REFUSED[who] if can(home, who, op, key)]
    assert not leaked, f"{who} is allowed beyond its scope: {leaked}"


def test_a_principal_may_not_be_the_root_key(home):
    r = _init(home, MODEL_READ_ACCESS_KEY=ROOT_USER)
    assert r.returncode != 0 and "root access key" in r.stderr


def test_half_a_key_pair_is_an_error_not_a_skip(home):
    r = _init(home, BACKUP_STORE_SECRET_KEY="")
    assert r.returncode != 0 and "must be set together" in r.stderr


def test_an_unconfigured_principal_is_skipped_and_says_so(home):
    r = _init(home, BACKUP_STORE_ACCESS_KEY="", BACKUP_STORE_SECRET_KEY="")
    assert r.returncode == 0 and "principal db-backup: not configured" in r.stdout
