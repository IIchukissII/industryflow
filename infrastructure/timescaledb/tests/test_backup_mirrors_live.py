# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The backup job's object mirrors and notebook archives against a real MinIO and Docker Engine
(ADR-0038 dec 1, 3-5; ADR-0032 dec 3-7; infrastructure/backup/db-backup.sh, notebook-backup.sh).

infrastructure/backup/tests runs these scripts against a stand-in `aws` and Engine API; here the
compose stack's own db-backup and notebook-backup run, with their own principals, and each result is
read back from the store as the stack's root:

- each database's dumps and the globals are kept to the newest RETAIN_PER_DB under their own prefix;
- every model and cold object is mirrored with the same bytes, upload staging left out;
- the documented copy-back of the model mirror reproduces the source's keys and sizes;
- an object gone from the source stays mirrored until the retention has passed, then leaves;
- a work volume is archived and restored with its contents, owners, modes and links, and its
  archives leave once the volume has been gone for the retention.

Needs the compose stack (MinIO with minio-init's buckets, the built db-backup image) and docker on
PATH; db-tenant-isolation.yml provides them. It deletes whatever is ageing in that stack's backups, so
it runs only with IF_REQUIRE_LIVE_STACK set, and then a missing stack is a failure.
"""
import hashlib
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
IMAGE = "industryflow-db-backup"
NETWORK = "industryflow-network"
DAY = 86400


def _skip(reason):
    if os.getenv("IF_REQUIRE_LIVE_STACK"):
        pytest.fail(f"required backup proof could not run: {reason}")
    pytest.skip(reason)


def _env_file():
    values = {}
    for line in (REPO / ".env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def _compose(*args, env=None):
    """A backup-profile one-shot, as the host cron runs it. The Docker socket's group is the runner's."""
    full_env = dict(os.environ, DOCKER_GID=str(os.stat("/var/run/docker.sock").st_gid), **(env or {}))
    return subprocess.run(["docker", "compose", "--profile", "backup", "run", "--rm", "-T", "--no-deps", *args],
                          cwd=REPO, capture_output=True, text=True, timeout=900, env=full_env)


def _ok(r, what):
    assert r.returncode == 0, f"{what} failed:\n{r.stdout}\n{r.stderr}"
    return r


class Root:
    """The store as its root sees it: aws-cli from the backup image, on the stack's network."""

    def __init__(self, env):
        self._creds = ["-e", f"AWS_ACCESS_KEY_ID={env['MINIO_ACCESS_KEY']}",
                       "-e", f"AWS_SECRET_ACCESS_KEY={env['MINIO_SECRET_KEY']}",
                       "-e", "AWS_DEFAULT_REGION=us-east-1"]

    def aws(self, *args, data=None):
        r = subprocess.run(["docker", "run", "--rm", "-i", "--network", NETWORK, *self._creds,
                            "--entrypoint", "aws", IMAGE, "--endpoint-url", "http://minio:9000", *args],
                           input=data, capture_output=True, timeout=300)
        assert r.returncode == 0, f"aws {' '.join(args)}: {r.stderr.decode()}"
        return r.stdout

    def put(self, bucket, key, data):
        self.aws("s3", "cp", "-", f"s3://{bucket}/{key}", data=data)

    def get(self, bucket, key):
        return self.aws("s3", "cp", f"s3://{bucket}/{key}", "-")

    def rm(self, bucket, key):
        self.aws("s3", "rm", f"s3://{bucket}/{key}")

    def listing(self, bucket, prefix):
        out = self.aws("s3api", "list-objects-v2", "--bucket", bucket, "--prefix", prefix,
                       "--query", "Contents[].[Key,Size]", "--output", "json")
        return {k: s for k, s in (json.loads(out) or [])}


def _docker(*args):
    r = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, f"docker {' '.join(args)}: {r.stderr}"
    return r.stdout


def _in_volume(volume, script):
    return _docker("run", "--rm", "--user", "0", "--network", "none", "-v", f"{volume}:/w",
                   "--entrypoint", "sh", IMAGE, "-c", script)


# Every entry's path, type, mode and owner, each file's bytes and each link's target. The image's
# tools are BusyBox (no `find -printf`), and pipefail keeps a failing step from leaving a short listing.
SNAPSHOT = ("set -eu -o pipefail; cd /w; find . -mindepth 1 | sort | while IFS= read -r p; do"
            " stat -c '%n %F %a %u:%g' \"$p\";"
            " if [ -L \"$p\" ]; then echo \"$p -> $(readlink \"$p\")\"; elif [ -f \"$p\" ]; then sha256sum \"$p\"; fi;"
            " done")


@pytest.fixture(scope="module")
def stack():
    if not os.getenv("IF_REQUIRE_LIVE_STACK"):
        # It writes real backups and ages mirrors and archives on a moved clock: whatever is ageing in
        # the stack it runs against is deleted. Only a stack marked disposable runs it.
        pytest.skip("runs only where IF_REQUIRE_LIVE_STACK=1 marks a disposable stack")
    if shutil.which("docker") is None:
        _skip("docker is not on PATH")
    if not (REPO / ".env").exists():
        _skip("no compose .env")
    if subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True).returncode != 0:
        _skip(f"the {IMAGE} image is not built")
    env = _env_file()
    root = Root(env)
    try:
        root.aws("s3api", "head-bucket", "--bucket", env.get("BACKUP_S3_BUCKET", "industryflow-backups"))
    except AssertionError as e:
        _skip(f"no MinIO with the backup bucket: {e}")
    return env, root


@pytest.fixture(scope="module")
def mirrored(stack):
    """Model and cold objects of a run of their own, then one backup with both mirrors."""
    env, root = stack
    run = uuid.uuid4().hex[:10]
    models, cold, backups = "mlflow", env.get("COLD_STORE_BUCKET", "industryflow-cold"), env.get(
        "BACKUP_S3_BUCKET", "industryflow-backups")
    seeded = {
        (models, f"tenant_ci{run}/1/{run}/artifacts/model/model.ubj"): os.urandom(300_000),
        (models, f"tenant_ci{run}/1/{run}/artifacts/model/MLmodel"): b"flavors: {}\n",
        (models, f"tenant_ci{run}/1/{run}/artifacts/a key with spaces.json"): b"{}",
        (cold, f"tenant_ci{run}/year=2026/month=11/day=05/part-0.parquet"): os.urandom(50_000),
        (cold, f"tenant_ci{run}/year=2026/month=11/day=05/_manifest.json"): b'{"parts": 1}',
    }
    staging = (models, f"_upload-staging/{run}/model.ubj")
    for (bucket, key), data in [*seeded.items(), (staging, b"unadmitted")]:
        root.put(bucket, key, data)
    _ok(_compose("db-backup"), "backup")
    yield dict(run=run, root=root, seeded=seeded, staging=staging, backups=backups,
               prefix={models: "models", cold: "cold"}, models=models)
    # Source and mirror left as they were before the seeding.
    prefix = {models: "models", cold: "cold"}
    for bucket, key in [*seeded, staging]:
        for b, k in ((bucket, key), (backups, f"{prefix[bucket]}/{key}")):
            try:
                root.rm(b, k)
            except AssertionError:
                pass


def test_every_object_is_mirrored_with_its_bytes_and_staging_is_left_out(mirrored):
    root, backups = mirrored["root"], mirrored["backups"]
    for (bucket, key), data in mirrored["seeded"].items():
        copy = root.get(backups, f"{mirrored['prefix'][bucket]}/{key}")
        assert hashlib.sha256(copy).digest() == hashlib.sha256(data).digest(), f"{bucket}/{key}"
    bucket, key = mirrored["staging"]
    assert root.listing(backups, f"models/{key}") == {}


def test_the_documented_copy_back_reproduces_the_model_bucket(mirrored):
    root, run = mirrored["root"], mirrored["run"]
    scratch = f"ci-restore-{run}"
    root.aws("s3", "mb", f"s3://{scratch}")
    try:
        # docs/operations/backup-and-recovery.md#model-artifacts, into a scratch bucket.
        root.aws("s3", "sync", f"s3://{mirrored['backups']}/models/", f"s3://{scratch}/", "--exclude", "_absent.tsv")
        prefix = f"tenant_ci{run}/"
        assert root.listing(scratch, prefix) == root.listing(mirrored["models"], prefix)
        assert root.listing(scratch, "_absent.tsv") == {}
    finally:
        root.aws("s3", "rb", f"s3://{scratch}", "--force")


def test_an_object_gone_from_the_source_leaves_the_mirror_after_the_retention(mirrored):
    root, backups = mirrored["root"], mirrored["backups"]
    gone, kept = list(k for b, k in mirrored["seeded"] if b == mirrored["models"])[:2]
    root.rm(mirrored["models"], gone)

    _ok(_compose("-e", "MIRROR_RETAIN_DAYS=1", "db-backup"), "backup after the delete")
    assert f"models/{gone}" in root.listing(backups, f"models/{gone}"), "left the mirror before the retention"
    assert gone in root.get(backups, "models/_absent.tsv").decode()

    later = str(int(time.time()) + 2 * DAY)
    _ok(_compose("-e", "MIRROR_RETAIN_DAYS=1", "-e", f"MIRROR_NOW={later}", "db-backup"), "backup after the retention")
    assert root.listing(backups, f"models/{gone}") == {}, "still mirrored after the retention"
    assert f"models/{kept}" in root.listing(backups, f"models/{kept}")
    assert gone not in root.get(backups, "models/_absent.tsv").decode()


def test_dumps_and_globals_are_kept_to_the_newest_under_each_prefix(stack):
    # Under a prefix of its own, so the stack's own dumps (the restore proofs read them) are untouched.
    env, root = stack
    backups = env.get("BACKUP_S3_BUCKET", "industryflow-backups")
    prefix = f"ci-retention-{uuid.uuid4().hex[:10]}"
    old = ("20200101T020000Z", "20200102T020000Z", "20200103T020000Z")
    for stamp in old:
        root.put(backups, f"{prefix}/industryflow/{stamp}.dump", b"old")
        root.put(backups, f"{prefix}/globals/{stamp}.sql", b"old")
    root.put(backups, f"{prefix}/industryflow_old/{old[0]}.dump", b"another prefix")
    try:
        r = _ok(_compose("-e", f"S3_PREFIX={prefix}", "-e", "RETAIN_PER_DB=2", "-e", "DATABASES=industryflow",
                         "db-backup"), "backup with a retention of 2")
        dumps, globals_ = (sorted(root.listing(backups, f"{prefix}/{p}/")) for p in ("industryflow", "globals"))
        assert len(dumps) == 2 and dumps[0] == f"{prefix}/industryflow/{old[2]}.dump", (dumps, r.stdout)
        assert len(globals_) == 2 and globals_[0] == f"{prefix}/globals/{old[2]}.sql", globals_
        new = dumps[1].rsplit("/", 1)[1].removesuffix(".dump")
        assert new > old[2] and globals_[1] == f"{prefix}/globals/{new}.sql"
        assert root.get(backups, dumps[1]) != b"old", "the run's own dump was not kept"
        assert list(root.listing(backups, f"{prefix}/industryflow_old/")) == [f"{prefix}/industryflow_old/{old[0]}.dump"]
    finally:
        for key in root.listing(backups, f"{prefix}/"):
            root.rm(backups, key)


@pytest.fixture
def work_volume(stack):
    run = uuid.uuid4().hex[:10]
    volume, target = f"industryflow-nb-ci{run}", f"ci-nb-restore-{run}"
    _docker("volume", "create", volume)
    _in_volume(volume, "set -e; mkdir -p /w/sub; head -c 200000 /dev/urandom > /w/analysis.ipynb;"
                       " printf 'a,b\\n1,2\\n' > /w/sub/data.csv; chmod 0600 /w/sub/data.csv;"
                       " ln -s analysis.ipynb /w/link; chown -R 1000:100 /w")
    yield volume, target
    for v in (volume, target):
        subprocess.run(["docker", "volume", "rm", "-f", v], capture_output=True)


def test_a_work_volume_is_archived_restored_and_aged_out(stack, work_volume):
    env, root = stack
    volume, target = work_volume
    backups = env.get("BACKUP_S3_BUCKET", "industryflow-backups")
    before = _in_volume(volume, SNAPSHOT)
    for entry in ("./analysis.ipynb regular file 644 1000:100", "./sub directory 755 1000:100",
                  "./sub/data.csv regular file 600 1000:100", "./link symbolic link 777 1000:100",
                  "./link -> analysis.ipynb"):
        assert entry in before.splitlines(), f"the snapshot does not hold {entry!r}:\n{before}"

    _ok(_compose("notebook-backup"), "notebook backup")
    archives = root.listing(backups, f"notebooks/{volume}/")
    assert len(archives) == 1, archives

    _ok(_compose("--entrypoint", "notebook-restore.sh", "notebook-backup", volume, "latest", "--target", target),
        "notebook restore")
    assert _in_volume(target, SNAPSHOT) == before

    # The volume goes; its archive stays until the retention has passed since its newest archive.
    _docker("volume", "rm", volume)
    _ok(_compose("-e", "MIRROR_RETAIN_DAYS=1", "notebook-backup"), "notebook backup without the volume")
    assert root.listing(backups, f"notebooks/{volume}/") == archives
    later = str(int(time.time()) + 2 * DAY)
    _ok(_compose("-e", "MIRROR_RETAIN_DAYS=1", "-e", f"MIRROR_NOW={later}", "notebook-backup"),
        "notebook backup after the retention")
    assert root.listing(backups, f"notebooks/{volume}/") == {}
