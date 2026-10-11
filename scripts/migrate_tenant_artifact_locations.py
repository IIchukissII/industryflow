#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Point MLflow's records for gateway-created experiments at the tenant prefix their bytes live under.

Before the tracking gateway recorded a tenant-prefixed artifact location (ADR-0019 dec 5), an
experiment it created was recorded as `mlflow-artifacts:/<id>` while every byte the kernel wrote went
to `tenant_<uuid>/<id>/...`. Anything that reads through MLflow directly, which is how the serving
side loads a model, then looked in `<id>/` and found nothing.

For each tenant-named experiment still recorded at the default `mlflow-artifacts:/<id>`, this checks
where its objects actually are:

  * under `tenant_<uuid>/<id>/` and not under `<id>/`  -> rewrite the records to the prefixed path;
  * no objects anywhere                                 -> rewrite (nothing moves; future writes match);
  * any objects under the un-prefixed `<id>/`           -> leave it, report it. Something trusted wrote
                                                           there directly, and those records are right.

It rewrites the experiment, its runs, its logged models, and model versions whose storage location
or source points into it. It never moves or deletes an object. Dry run by default; `--apply` writes
in one transaction. Re-running is safe: a migrated experiment no longer matches.

Run inside the MLflow container, which has the database URI and object-store credentials:

    docker compose exec -T mlflow python - < scripts/migrate_tenant_artifact_locations.py
    docker compose exec -T mlflow python - --apply < scripts/migrate_tenant_artifact_locations.py
"""

from __future__ import annotations

import os
import re
import sys

TENANT_NAME = re.compile(r"^(tenant_[0-9a-f]{8}_[0-9a-f]{4}_[0-9a-f]{4}_[0-9a-f]{4}_[0-9a-f]{12})\.")
SCHEME = "mlflow-artifacts:/"


def plan(experiments, count_objects):
    """Decide per experiment. `experiments` is [(id, name, artifact_location)]; `count_objects(prefix)`
    returns how many objects exist under an object-store key prefix. Returns (migrate, skipped), where
    migrate is [(id, old_root, new_root)] and skipped is [(id, name, reason)]."""
    migrate, skipped = [], []
    for exp_id, name, location in experiments:
        m = TENANT_NAME.match(name or "")
        if not m or location != f"{SCHEME}{exp_id}":
            continue
        token = m.group(1)
        unprefixed = count_objects(f"{exp_id}/")
        if unprefixed:
            skipped.append((exp_id, name, f"{unprefixed} object(s) under the un-prefixed {exp_id}/"))
            continue
        migrate.append((exp_id, f"{SCHEME}{exp_id}", f"{SCHEME}{token}/{exp_id}"))
    return migrate, skipped


def rewrite_statements(old_root: str, new_root: str):
    """(sql, params) pairs that move every record under `old_root/` (and the root itself) to `new_root`.
    Prefix matching is by substring equality, not LIKE: `_` is a LIKE wildcard and ids are not escaped."""
    n = len(old_root) + 1
    p = {"old": old_root, "old_slash": old_root + "/", "new": new_root, "n": n}
    tail = "substr({col}, :n + 1)"
    under = "substr({col}, 1, :n) = :old_slash"
    out = [("UPDATE experiments SET artifact_location = :new WHERE artifact_location = :old", p)]
    for table, col in (("runs", "artifact_uri"), ("logged_models", "artifact_location"),
                       ("model_versions", "storage_location"), ("model_versions", "source")):
        out.append((f"UPDATE {table} SET {col} = :new || '/' || {tail.format(col=col)} "
                    f"WHERE {under.format(col=col)}", p))
    return out


def main(argv) -> int:
    import boto3
    import sqlalchemy as sa

    apply = "--apply" in argv
    bucket = os.environ.get("ARTIFACT_BUCKET", "mlflow")
    s3 = boto3.client("s3", endpoint_url=os.environ.get("MLFLOW_S3_ENDPOINT_URL"))

    def count_objects(prefix: str) -> int:
        r = s3.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=1000)
        return r.get("KeyCount", 0)

    engine = sa.create_engine(os.environ["BACKEND_STORE_URI"])
    with engine.connect() as c:
        rows = c.execute(sa.text("SELECT experiment_id, name, artifact_location FROM experiments")).fetchall()
    migrate, skipped = plan([(str(r[0]), r[1], r[2]) for r in rows], count_objects)

    for exp_id, name, reason in skipped:
        print(f"skip    {exp_id} {name}: {reason}")
    for exp_id, old, new in migrate:
        print(f"{'migrate' if apply else 'would  '} {exp_id}: {old} -> {new}")
    if not migrate:
        print("nothing to migrate")
        return 0
    if not apply:
        print("dry run; re-run with --apply to write")
        return 0
    with engine.begin() as c:
        for _, old, new in migrate:
            for sql, params in rewrite_statements(old, new):
                c.execute(sa.text(sql), params)
    print(f"migrated {len(migrate)} experiment(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
