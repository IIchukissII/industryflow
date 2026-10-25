# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The compose side of the database roles: every role password an init script reads is passed to
the migration runner that applies the scripts (ADR-0034) and templated in .env.example, the monitoring plane never logs in as the superuser
(ADR-0016 dec 3), and no shared dashboard can query tenant data (ADR-0016 dec 5)."""

from __future__ import annotations

import re

from _repo import ROOT, load_yaml

COMPOSE = load_yaml("docker-compose.yml")["services"]


def _init_script_passwords() -> set[str]:
    found = set()
    for script in (ROOT / "infrastructure" / "timescaledb" / "init-scripts").iterdir():
        found |= set(re.findall(r"\$\{?([A-Z_]+_DB_PASSWORD)", script.read_text()))
    assert found, "no init script reads a role password — the guard is not looking"
    return found


def _env_example() -> set[str]:
    text = (ROOT / ".env.example").read_text()
    return set(re.findall(r"^([A-Z0-9_]+)=", text, re.M))


def test_every_role_password_reaches_the_migration_runner():
    env = COMPOSE["db-migrate"]["environment"]
    missing = _init_script_passwords() - set(env)
    assert not missing, f"init scripts read passwords compose never passes: {sorted(missing)}"


def test_the_database_container_holds_no_role_password():
    # The scripts run in db-migrate, not under the image's init hook; the server needs none of them.
    held = {k for k in COMPOSE["timescaledb"]["environment"] if k.endswith("_DB_PASSWORD")}
    assert not held, f"the database container still receives role passwords: {sorted(held)}"


def test_every_role_password_is_templated_in_env_example():
    missing = _init_script_passwords() - _env_example()
    assert not missing, f".env.example lacks: {sorted(missing)}"


def test_postgres_exporter_uses_the_monitoring_role_over_verified_tls():
    env = COMPOSE["postgres-exporter"]["environment"]
    assert env["DATA_SOURCE_USER"] == "metrics_user"
    assert "METRICS_DB_PASSWORD" in env["DATA_SOURCE_PASS"]
    assert "DB_PASSWORD}" not in env["DATA_SOURCE_PASS"].replace("METRICS_DB_PASSWORD}", "")
    assert "verify-full" in env["DATA_SOURCE_URI"]


def test_grafana_has_no_database_datasource():
    sources = load_yaml("infrastructure/grafana/provisioning/datasources.yml")["datasources"]
    db = [s["name"] for s in sources if s["type"] in {"postgres", "grafana-postgresql-datasource", "mysql"}]
    assert not db, f"the shared monitoring plane must not query tenant data: {db}"
    assert "DB_PASSWORD" not in COMPOSE["grafana"].get("environment", {})


def test_the_default_client_ca_mount_holds_the_ca_db_cert_init_staged():
    # A bare `up` (TLS_CERT_DIR unset) must still verify-full: whatever volume the clients mount as
    # their CA by default has to be one db-cert-init writes ca.crt into.
    mount = next(v for v in COMPOSE["mlflow"]["volumes"] if v.endswith(":/etc/ssl/industryflow:ro"))
    default = re.fullmatch(r"\$\{TLS_CERT_DIR:-([\w-]+)\}:/etc/ssl/industryflow:ro", mount)
    assert default, f"unexpected CA mount {mount!r}"
    init = COMPOSE["db-cert-init"]
    targets = [v.split(":")[1] for v in init["volumes"] if v.split(":")[0] == default.group(1)]
    assert targets, f"db-cert-init does not mount the clients' default CA volume {default.group(1)!r}"
    script = "\n".join(init["command"])
    assert any(re.search(rf"cp \S+ {re.escape(t)}/ca\.crt", script) for t in targets), (
        f"db-cert-init never writes ca.crt into {targets}"
    )
