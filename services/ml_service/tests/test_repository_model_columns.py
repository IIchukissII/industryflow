# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A column the write path stores and the read path drops is a column that does not exist.

This is the check for a shape that has now bitten repeatedly in this arc: an explicit list that must
be remembered, whose omission is silent. The repository declares its columns FOUR times — an insert
allowlist, two SELECT lists, and the row→dict mapping — and each is a separate chance to forget.

The upload path forgot three of them. `provenance`/`artifact_uri` were added to the insert mapping,
the model registered fine, and the deployment gate then died with `KeyError: 'artifact_uri'` on a
real box, after every unit test had passed — because the reads never selected the columns and the
mapping never surfaced them.

These tests read the module's own source. That is unusual and deliberate: the alternative is a live
Postgres, and the failure mode here is not a query that errors — it is a query that succeeds and
returns less than it should.
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

_REPO_SRC = os.path.join(os.path.dirname(__file__), "..", "api", "repository.py")

# Columns the model surface must carry end to end. Not every column in the table — the ones whose
# loss is silent and whose absence breaks a gate.
_MUST_ROUND_TRIP = ("provenance", "artifact_uri", "compatibility_status")


def _source():
    with open(_REPO_SRC) as fh:
        return fh.read()


def _full_surface_selects(src):
    """The SELECT lists that feed the model surface — not every query against the table.

    Narrow reads exist and are fine: "which active model of this type" wants six columns, and
    demanding it fetch provenance would be noise, not safety. A full-surface read is one that asks
    where the model came from, so `mlflow_run_id` is the marker — the reads that care about a
    model's origin are exactly the ones that must carry the rest of it.
    """
    return [
        columns
        # No SELECT inside the list: an earlier, unrelated SELECT must not open a match that runs on
        # to the next `FROM ml_models` and swallows the code in between.
        for columns in re.findall(r"SELECT\s+((?:(?!SELECT).)*?)\s+FROM ml_models", src, re.S)
        if re.search(r"\bmlflow_run_id\b", columns)
    ]


def _insert_mapping(src):
    block = re.search(r"field_mapping = \{(.*?)\}", src, re.S)
    return set(re.findall(r"'(\w+)':", block.group(1)))


def _row_mapping(src):
    block = re.search(r"def _format_model_row.*?\n    return \{(.*?)\n    \}", src, re.S)
    return set(re.findall(r"'(\w+)':", block.group(1)))


def test_the_reads_actually_select_the_columns():
    src = _source()
    lists = _full_surface_selects(src)
    assert len(lists) == 2, f"expected 2 full-surface reads, found {len(lists)} — this check has gone stale"
    for column in _MUST_ROUND_TRIP:
        for i, columns in enumerate(lists):
            assert re.search(rf"\b{column}\b", columns), (
                f"SELECT list #{i + 1} does not fetch `{column}`. The query will succeed and return "
                f"less than it should, which is how this failed the first time."
            )


def test_the_row_mapping_surfaces_the_columns():
    mapping = _row_mapping(_source())
    for column in _MUST_ROUND_TRIP:
        assert column in mapping, f"_format_model_row drops `{column}`, so no caller can ever see it"


def test_the_write_path_persists_the_columns():
    mapping = _insert_mapping(_source())
    for column in ("provenance", "artifact_uri"):
        assert column in mapping, (
            f"create_model's field_mapping omits `{column}`, so it is silently discarded on insert"
        )


def test_the_api_actually_returns_provenance():
    """The last list in the chain, and the one that made the other three pointless.

    `GET /api/models` declares a response model, and pydantic drops any field it does not name —
    however faithfully the DB, the SELECT and the row mapping carried it. Provenance was stored,
    fetched, mapped, and then filtered out at the door: ADR-0030 dec 8 records it as a first-class
    fact precisely so an operator can SEE it, and a fact nobody can read is not one the platform
    holds. Asserted through the real model rather than by reading source, because the filtering IS
    the behaviour under test.
    """
    import datetime
    from routers.models import ModelMetadata

    out = ModelMetadata(
        model_id="m1", company_id="c1", model_name="n", model_version="1", model_type="outlier",
        status="production", created_at=datetime.datetime.now(), provenance="uploaded",
    ).model_dump()
    assert out.get("provenance") == "uploaded", "the response model drops provenance at the door"


def test_the_api_actually_returns_description():
    """The same door provenance was dropped at, for the same reason.

    `description` is SELECTed by both full-surface reads and surfaced by `_format_model_row`, so the
    DB, the query, and the mapping all carry it — but the response model dropped it, so no operator
    could ever read it. It is load-bearing for an uploaded model (ADR-0030), whose author writes a
    description the platform never watched them earn; losing it silently is the expensive shape this
    file exists to catch.
    """
    import datetime
    from routers.models import ModelMetadata

    out = ModelMetadata(
        model_id="m1", company_id="c1", model_name="n", model_version="1", model_type="outlier",
        status="production", created_at=datetime.datetime.now(), description="what this predicts",
    ).model_dump()
    assert out.get("description") == "what this predicts", "the response model drops description at the door"


def test_every_column_the_writes_persist_can_be_read_back():
    """The asymmetry itself, as a property: anything the insert allowlist accepts should be
    selectable and mappable. This is what nobody checked."""
    src = _source()
    written = _insert_mapping(src)
    mapped = _row_mapping(src)
    # `feature_config_id` and friends are read via other surfaces; only assert the ADR-0030 columns
    # plus the ADR-0027 verdict, which have gates depending on them.
    for column in _MUST_ROUND_TRIP:
        if column in written:
            assert column in mapped, f"`{column}` is written but never read back"


# --- against the schema itself -------------------------------------------------------------------
#
# The checks above compare the repository's lists with each other. Two failures slipped between
# them: `training_duration_seconds` sat in the insert allowlist for a column no migration ever
# created (any registration that sent it failed), and `description` was SELECTed, mapped and
# advertised as load-bearing while nothing wrote it. Both are caught only by reading the schema, and
# by asking of every column the read path surfaces whether anything can put a value there.

_INIT = os.path.join(os.path.dirname(__file__), "..", "..", "..", "infrastructure", "timescaledb", "init-scripts")

# Columns the server owns: assigned by the database or by a lifecycle transition, never registered.
_SERVER_OWNED = {"model_id", "created_at", "updated_at", "deployed_at", "deprecated_at"}


def _ml_models_columns():
    import glob
    columns = set()
    for path in sorted(glob.glob(os.path.join(_INIT, "*.sql"))):
        with open(path) as fh:
            sql = fh.read()
        create = re.search(r"CREATE TABLE %I\.ml_models \((.*?)\n\s*\)\s*'", sql, re.S)
        if create:
            for line in create.group(1).splitlines():
                m = re.match(r"\s*([a-z_0-9]+)\s+[A-Z]", line)
                if m:
                    columns.add(m.group(1))
        for alter in re.findall(r"ALTER TABLE %I\.ml_models(.*?)'", sql, re.S):
            columns.update(re.findall(r"ADD COLUMN IF NOT EXISTS (\w+)", alter))
    return columns


def test_the_schema_is_readable_here():
    cols = _ml_models_columns()
    assert {"model_id", "model_name", "description", "provenance", "detector"} <= cols, cols


def test_every_column_the_write_path_names_exists():
    missing = _insert_mapping(_source()) - _ml_models_columns()
    assert not missing, (
        f"create_model would INSERT into {sorted(missing)}, which no migration creates — every "
        f"registration that sends one fails"
    )


def test_every_column_the_reads_select_exists():
    cols = _ml_models_columns()
    for i, selected in enumerate(_full_surface_selects(_source())):
        names = set(re.findall(r"\b([a-z_]+)\b", selected))
        assert names <= cols, f"SELECT list #{i + 1} names {sorted(names - cols)}, which do not exist"


def test_every_column_the_read_path_surfaces_can_be_written():
    """A column the API shows and nothing writes is a promise the platform cannot keep."""
    surfaced = _row_mapping(_source()) & _ml_models_columns()
    unwritable = surfaced - _insert_mapping(_source()) - _SERVER_OWNED
    assert not unwritable, f"{sorted(unwritable)} are read and shown, but registration never writes them"


def test_the_detector_round_trips():
    """Inference dispatches on it (ADR-0010 dec 6); a read that dropped it would score every model
    by whatever default the caller assumed — which is what happened while no column carried it."""
    src = _source()
    assert "detector" in _insert_mapping(src)
    assert "detector" in _row_mapping(src)
    for columns in _full_surface_selects(src):
        assert re.search(r"\bdetector\b", columns)


def test_the_api_carries_the_detector():
    import datetime
    from routers.models import ModelMetadata

    out = ModelMetadata(
        model_id="m1", company_id="c1", model_name="n", model_version="1", model_type="outlier",
        status="production", created_at=datetime.datetime.now(), detector="outlier",
    ).model_dump()
    assert out.get("detector") == "outlier"


def test_registration_accepts_only_what_the_table_can_hold():
    """Every registration field is a column, except the one the gate judges and does not store."""
    from routers.models import ModelCreateRequest

    fields = set(ModelCreateRequest.model_fields)
    assert {"description", "detector"} <= fields, "registration cannot write them"
    phantom = fields - _ml_models_columns() - {"score_semantics"}
    assert not phantom, f"registration accepts {sorted(phantom)}, which no column holds"
