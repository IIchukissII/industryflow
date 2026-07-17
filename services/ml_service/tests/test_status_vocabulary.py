# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A status the API can set but the DB rejects is a 500 waiting to happen.

`ml_models.status` has a CHECK constraint — one authoritative vocabulary — and two places name it a
second time: the deploy endpoint's `valid_statuses`, and the soft-delete's UPDATE. Both had drifted
to MLflow's stage names (`staging`, `archived`), which this table never adopted, so deploying to
either or deleting a model wrote a value the constraint refuses and the request 500'd. That is the
silent-list-drift class this repo keeps paying for: a value list that must match another value list,
with no compiler to notice when it stops.

These tests read the source (the SQL and the two callers), because the failure is not a query that
errors in isolation — it is a query whose input the DB will reject, which only a live Postgres would
show. Asserting containment against the CHECK is the guard that a unit test can hold.
"""
import os
import re

_HERE = os.path.dirname(__file__)
_INIT = os.path.join(_HERE, "..", "..", "..", "infrastructure", "timescaledb", "init-scripts",
                     "03-tenant-ml-tables.sql")
_MODELS = os.path.join(_HERE, "..", "api", "routers", "models.py")
_REPO = os.path.join(_HERE, "..", "api", "repository.py")


def _read(path):
    with open(path) as fh:
        return fh.read()


def _db_status_set():
    """The values `ml_models.status` actually permits. The constraint lives inside a PL/pgSQL string,
    so the single-quotes are doubled (`''training''`)."""
    src = _read(_INIT)
    m = re.search(r"status\s+IN\s*\((.*?)\)", src, re.S | re.I)
    assert m, "could not find the ml_models status CHECK — this guard has gone stale"
    return set(re.findall(r"''(\w+)''", m.group(1)))


def test_the_db_vocabulary_is_the_one_we_expect():
    # A canary: if the DB vocabulary itself changes, the containment tests below still pass silently
    # (a superset admits everything), so pin what the source of truth currently says out loud.
    assert _db_status_set() == {"training", "active", "production", "deprecated", "failed"}


def test_deploy_targets_are_all_db_valid():
    src = _read(_MODELS)
    m = re.search(r"valid_statuses\s*=\s*\[(.*?)\]", src, re.S)
    assert m, "could not find the deploy endpoint's valid_statuses"
    deploy = set(re.findall(r"'(\w+)'", m.group(1)))
    assert deploy, "valid_statuses parsed empty"
    extra = deploy - _db_status_set()
    assert not extra, (
        f"the deploy endpoint offers status value(s) the ml_models CHECK rejects: {sorted(extra)} — "
        f"choosing one 500s. The DB permits {sorted(_db_status_set())}."
    )


def test_soft_delete_target_is_db_valid():
    m = re.search(r"UPDATE ml_models SET status = '(\w+)' WHERE model_id", _read(_REPO))
    assert m, "could not find the soft-delete status update"
    target = m.group(1)
    assert target in _db_status_set(), (
        f"delete_model sets status='{target}', which the ml_models CHECK rejects — the delete 500s. "
        f"The DB's retire state is 'deprecated'."
    )
