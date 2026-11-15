# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The runbook rows are data the agent owns, read as a contract (ADR-0046 dec 9)."""

import tomllib

import runbooks
from conftest import REPO, SERVICE

DATA = SERVICE / "runbook.toml"


def test_the_rows_load_and_every_field_is_filled():
    rows, alerts = runbooks.load(DATA), runbooks.load_alerts(DATA)
    assert len(rows) >= 15 and len(alerts) >= 10, "the agent's runbook rows are there"
    assert all(r.symptom and r.cause and r.fix and r.source for r in rows)
    assert all(a.fires_when and a.handling and a.source for a in alerts.values())


def test_every_symptom_table_keeps_the_contract_shape():
    """A row with another key, or missing one, is a row the agent misreads; nothing else would say so."""
    data = tomllib.loads(DATA.read_text())
    assert set(data) == {"alert", "symptom"}
    assert all(set(a) == {"name", "fires_when", "handling", "source"} for a in data["alert"])
    assert all(set(r) == {"symptom", "cause", "fix", "source"} for r in data["symptom"])
    names = [a["name"] for a in data["alert"]]
    assert len(names) == len(set(names)), "an alert has one row"


def test_rows_match_by_shared_words_best_first():
    rows = [runbooks.Row("Redis refuses writes", "memory limit reached", "raise maxmemory", "a.md"),
            runbooks.Row("Kafka lag grows", "consumer stopped", "restart the consumer", "b.md")]
    assert [r.source for r in runbooks.match(rows, "IndustryFlowRedisMemoryHigh: Redis is near its memory limit")] == ["a.md"]
    assert runbooks.match(rows, "something else entirely") == []


def test_a_single_shared_word_is_not_a_match():
    """Measured on the box's alerts: one shared word (`warning`, `readings`) matched unrelated rows."""
    rows = [runbooks.Row("CRL refresh unhealthy", "a warning in the log", "re-sign", "a.md")]
    assert runbooks.match(rows, "Readings were dead-lettered warning") == []


def test_every_platform_rule_has_a_row_in_the_alert_table():
    import yaml
    names = {r["alert"] for f in sorted((REPO / "infrastructure/prometheus/rules").glob("*.yml"))
             for g in yaml.safe_load(f.read_text())["groups"] for r in g["rules"]}
    assert "IndustryFlowBackupStale" in names, "the rules files were not read"
    table = runbooks.load_alerts(DATA)
    assert names and not names - set(table), f"platform alerts with no alert row: {sorted(names - set(table))}"
