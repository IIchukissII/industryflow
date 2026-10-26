# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
notebook-backup.sh and notebook-restore.sh (ADR-0038 dec 4-5, 8), run for real under bash against a
stand-in Docker Engine API (fake_docker_curl.py) and the stand-in object store (fake_aws.py).
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
STORE = "backup-store_9000"
DAY = 86400
T0 = 1_760_000_000  # 2025-10-09T08:53:20Z


def _exe(path: Path, target: str):
    path.write_text(f"#!{sys.executable}\nimport runpy, sys\nsys.argv[0] = {path.name!r}\n"
                    f"runpy.run_path({target!r}, run_name='__main__')\n")
    path.chmod(0o755)


@pytest.fixture
def env(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _exe(bin_dir / "aws", str(HERE / "fake_aws.py"))
    _exe(bin_dir / "curl", str(HERE / "fake_docker_curl.py"))
    docker = tmp_path / "docker"
    (docker / "volumes").mkdir(parents=True)
    (tmp_path / "s3").mkdir()
    e = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path), "FAKE_S3_ROOT": str(tmp_path / "s3"),
         "FAKE_DOCKER_ROOT": str(docker), "S3_BUCKET": "backups", "S3_ENDPOINT_URL": "http://backup-store:9000",
         "AWS_ACCESS_KEY_ID": "backup-writer", "AWS_SECRET_ACCESS_KEY": "backup-secret",
         "NOTEBOOK_HELPER_IMAGE": "industryflow-db-backup", "RETAIN_PER_VOLUME": "2"}
    return {"env": e, "docker": docker, "s3": tmp_path / "s3" / STORE / "backups"}


def _volume(env, name, files: dict):
    root = env["docker"] / "volumes" / name
    for rel, data in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _backup(env, stamp, **extra):
    e = dict(env["env"], NOTEBOOK_BACKUP_STAMP=stamp, **extra)
    return subprocess.run(["bash", str(SCRIPTS / "notebook-backup.sh")], env=e, capture_output=True, text=True)


def _restore(env, *args, **extra):
    e = dict(env["env"], **extra)
    return subprocess.run(["bash", str(SCRIPTS / "notebook-restore.sh"), *args], env=e, capture_output=True, text=True)


def _archives(env, volume):
    d = env["s3"] / "notebooks" / volume
    return sorted(p.name for p in d.iterdir()) if d.exists() else []


def _calls(env):
    return [json.loads(line) for line in (env["docker"] / "calls.log").read_text().splitlines()]


ALICE = "industryflow-nb-alice"


def test_each_work_volume_is_archived_and_nothing_else(env):
    _volume(env, ALICE, {"analysis.ipynb": b'{"cells": []}', "data/x.csv": b"1,2"})
    _volume(env, "industryflow_timescaledb-data", {"PG_VERSION": b"15"})
    r = _backup(env, "20261026T020000Z")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _archives(env, ALICE) == ["20261026T020000Z.tar.gz"]
    assert not (env["s3"] / "notebooks" / "industryflow_timescaledb-data").exists()
    # Read through a container that is never started, has no network, mounts the volume read-only,
    # and is removed again.
    creates = [c for c in _calls(env) if c["path"] == "/containers/create"]
    assert len(creates) == 1
    spec = json.loads(creates[0]["body"])
    assert spec["HostConfig"]["Binds"] == [f"{ALICE}:/w:ro"]
    assert spec["HostConfig"]["NetworkMode"] == "none" and spec["NetworkDisabled"] is True
    assert spec["HostConfig"]["ReadonlyRootfs"] is True
    assert not any(c["path"].endswith("/start") for c in _calls(env))
    assert not list((env["docker"] / "containers").iterdir())


def test_archives_are_kept_by_count(env):
    _volume(env, ALICE, {"a.ipynb": b"1"})
    for stamp in ("20261024T020000Z", "20261025T020000Z", "20261026T020000Z"):
        assert _backup(env, stamp).returncode == 0
    assert _archives(env, ALICE) == ["20261025T020000Z.tar.gz", "20261026T020000Z.tar.gz"]


def test_a_gone_volume_keeps_its_archives_one_retention_then_loses_them(env):
    # ADR-0038 dec 4 / ADR-0033 dec 4: a deleted tenant's notebooks do not outlive the retention.
    root = _volume(env, ALICE, {"a.ipynb": b"1"})
    _volume(env, "industryflow-nb-bob", {"b.ipynb": b"2"})
    assert _backup(env, "20251009T085320Z").returncode == 0  # = T0
    for p in sorted(root.rglob("*"), reverse=True):
        p.unlink() if p.is_file() else p.rmdir()
    root.rmdir()
    r = _backup(env, "20251010T085320Z", MIRROR_NOW=str(T0 + DAY), MIRROR_RETAIN_DAYS="14")
    assert r.returncode == 0 and _archives(env, ALICE) == ["20251009T085320Z.tar.gz"], r.stdout + r.stderr
    r = _backup(env, "20251023T085319Z", MIRROR_NOW=str(T0 + 14 * DAY - 1), MIRROR_RETAIN_DAYS="14")
    assert r.returncode == 0 and _archives(env, ALICE)
    r = _backup(env, "20251023T085320Z", MIRROR_NOW=str(T0 + 14 * DAY), MIRROR_RETAIN_DAYS="14")
    assert r.returncode == 0, r.stdout + r.stderr
    assert _archives(env, ALICE) == []
    assert _archives(env, "industryflow-nb-bob")  # a volume that still exists keeps its archives


def test_a_restore_into_a_scratch_volume_matches_the_archive(env):
    _volume(env, ALICE, {"analysis.ipynb": b'{"cells": [1]}', "deep/dir/notes.md": b"# n"})
    assert _backup(env, "20261026T020000Z").returncode == 0
    r = _restore(env, ALICE, "latest", "--target", "industryflow-nb-alice-rehearsal")
    assert r.returncode == 0, r.stdout + r.stderr
    restored = env["docker"] / "volumes" / "industryflow-nb-alice-rehearsal"
    assert (restored / "analysis.ipynb").read_bytes() == b'{"cells": [1]}'
    assert (restored / "deep/dir/notes.md").read_bytes() == b"# n"
    assert "entries match the archive" in r.stdout


def test_a_restore_that_wrote_less_than_the_archive_fails(env):
    _volume(env, ALICE, {"analysis.ipynb": b"1", "notes.md": b"2"})
    assert _backup(env, "20261026T020000Z").returncode == 0
    r = _restore(env, ALICE, "--target", "scratch", FAKE_DOCKER_DROP_ON_PUT="notes.md")
    assert r.returncode != 0
    assert "differs from the archive" in r.stdout


def test_a_restore_never_overwrites_an_existing_volume(env):
    _volume(env, ALICE, {"analysis.ipynb": b"current"})
    assert _backup(env, "20261026T020000Z").returncode == 0
    r = _restore(env, ALICE)
    assert r.returncode != 0 and "already exists" in r.stdout
    assert (env["docker"] / "volumes" / ALICE / "analysis.ipynb").read_bytes() == b"current"
