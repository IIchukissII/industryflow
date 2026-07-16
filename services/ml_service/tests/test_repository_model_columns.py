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
        for columns in re.findall(r"SELECT\s+(.*?)\s+FROM ml_models", src, re.S)
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
