# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The shared monitoring Grafana never gets a database datasource (ADR-0016 dec 5): the database holds
tenant data. Provisioning never removes a datasource a file stops listing, so the one an earlier
version created is deleted explicitly."""

from __future__ import annotations

from _repo import load_yaml

DATASOURCES = load_yaml("infrastructure/grafana/provisioning/datasources.yml")
DATABASE_TYPES = {"postgres", "grafana-postgresql-datasource", "mysql", "mssql"}


def test_no_database_datasource_is_provisioned():
    kinds = {d["name"]: d["type"] for d in DATASOURCES["datasources"]}
    assert kinds and not DATABASE_TYPES & set(kinds.values()), kinds


def test_the_earlier_database_datasource_is_deleted():
    assert {"name": "TimescaleDB", "orgId": 1} in DATASOURCES.get("deleteDatasources", [])
