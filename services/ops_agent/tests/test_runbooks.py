# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The runbooks' Symptom | Cause | Fix tables are a contract the agent reads (ADR-0046 dec 9)."""


import runbooks
from conftest import REPO

DOCS = REPO / "docs" / "operations"


def test_the_real_runbooks_parse_into_rows():
    rows = runbooks.load(DOCS)
    assert len(rows) >= 15, "the operations runbooks hold their troubleshooting tables"
    assert all(r.symptom and r.cause and r.fix for r in rows)


def test_every_symptom_table_keeps_the_contract_shape():
    """A table that starts with a Symptom column must be exactly Symptom | Cause | Fix, or the agent
    stops reading it and nothing says so."""
    bad = []
    for path in sorted(DOCS.rglob("*.md")):
        for n, line in enumerate(path.read_text().splitlines(), 1):
            cells = [c.strip().lower() for c in line.strip().strip("|").split("|")]
            if line.lstrip().startswith("|") and cells and cells[0] == "symptom" and cells != ["symptom", "cause", "fix"]:
                bad.append(f"{path.name}:{n}: {line.strip()}")
    assert not bad, f"runbook tables the agent cannot read: {bad}"


def test_rows_match_by_shared_words_best_first():
    rows = [runbooks.Row("Redis refuses writes", "memory limit reached", "raise maxmemory", "a.md"),
            runbooks.Row("Kafka lag grows", "consumer stopped", "restart the consumer", "b.md")]
    assert [r.source for r in runbooks.match(rows, "IndustryFlowRedisMemoryHigh: Redis is near its memory limit")] == ["a.md"]
    assert runbooks.match(rows, "something else entirely") == []


def test_a_table_is_read_until_it_ends():
    text = "x\n| Symptom | Cause | Fix |\n|---|---|---|\n| a | b | c |\n| d | e | f |\n\n| Symptom | Other |\n|---|---|\n| g | h |\n"
    assert [(r.symptom, r.fix) for r in runbooks.parse(text, "t.md")] == [("a", "c"), ("d", "f")]


def test_a_single_shared_word_is_not_a_match():
    """Measured on the box's alerts: one shared word (`warning`, `readings`) matched unrelated rows."""
    rows = [runbooks.Row("CRL refresh unhealthy", "a warning in the log", "re-sign", "a.md")]
    assert runbooks.match(rows, "Readings were dead-lettered warning") == []


def test_every_platform_rule_has_a_row_in_the_alert_table():
    import yaml
    rules = yaml.safe_load((REPO / "infrastructure/prometheus/rules/platform.yml").read_text())
    names = {r["alert"] for g in rules["groups"] for r in g["rules"]}
    table = runbooks.load_alerts(DOCS)
    assert names and not names - set(table), f"platform alerts with no alert-table row: {sorted(names - set(table))}"
