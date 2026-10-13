# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The gateway's database footprint fits its role's CONNECTION LIMIT, in every shipped deployment.

Every uvicorn worker holds its own pool, so the most connections api_gateway_user can open is
replicas x workers x (pool size + overflow). Postgres refuses the excess with "too many
connections for role" (an outage under load, not an error at deploy). This reads both sides from
the artifacts that set them: the role limit from the init script, and the compose and Helm
defaults (with the Dockerfile and Settings fallbacks behind them).
"""
import os
import re

import pytest
import yaml

from config import Settings

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
# Kept free for scripts/manage_users.py and scripts/seed_users.py, which connect as the same role
# (one connection each).
ADMIN_HEADROOM = 2


def _read(*parts):
    with open(os.path.join(REPO, *parts), encoding="utf-8") as f:
        return f.read()


def _role_limit() -> int:
    script = _read("infrastructure", "timescaledb", "init-scripts", "00-create-roles.sh")
    m = re.search(r"ALTER ROLE api_gateway_user CONNECTION LIMIT (\d+);", script)
    assert m, "api_gateway_user has no CONNECTION LIMIT"
    return int(m.group(1))


def _dockerfile_workers() -> int:
    dockerfile = _read("services", "api_gateway", "Dockerfile")
    assert '--workers \\"$API_GATEWAY_WORKERS\\"' in dockerfile, "CMD must take workers from the env"
    m = re.search(r"^ENV API_GATEWAY_WORKERS=(\d+)$", dockerfile, re.M)
    assert m, "Dockerfile must default API_GATEWAY_WORKERS"
    return int(m.group(1))


def _settings_default(name: str) -> int:
    return int(Settings.model_fields[name].default)


def _env_example() -> dict:
    out = {}
    for line in _read(".env.example").splitlines():
        m = re.match(r"^([A-Z0-9_]+)=(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def _compose_value(env: dict, name: str, fallback: int) -> int:
    """Resolve one api-gateway env value the way compose does with .env copied from .env.example."""
    raw = env.get(name)
    if raw is None:
        return fallback
    m = re.fullmatch(r"\$\{(\w+)(?::-(.*))?\}", str(raw))
    if not m:
        return int(raw)
    value = _env_example().get(m.group(1)) or m.group(2)
    assert value, f"{name} resolves to nothing"
    return int(value)


def test_compose_default_fits_the_role_limit():
    compose = yaml.safe_load(_read("docker-compose.yml"))
    gw = compose["services"]["api-gateway"]
    # A command override would bypass API_GATEWAY_WORKERS (it once pinned --workers by hand).
    assert "command" not in gw, "api-gateway must run the image CMD so API_GATEWAY_WORKERS applies"
    env = gw["environment"]
    workers = _compose_value(env, "API_GATEWAY_WORKERS", _dockerfile_workers())
    per_worker = (_compose_value(env, "API_GATEWAY_DB_POOL_SIZE", _settings_default("API_GATEWAY_DB_POOL_SIZE"))
                  + _compose_value(env, "API_GATEWAY_DB_MAX_OVERFLOW", _settings_default("API_GATEWAY_DB_MAX_OVERFLOW")))
    replicas = 1  # one api-gateway container (fixed container_name)
    assert replicas * workers * per_worker <= _role_limit() - ADMIN_HEADROOM


def test_helm_default_fits_the_role_limit_at_full_scale_out():
    values = yaml.safe_load(_read("deploy", "helm", "industryflow", "values.yaml"))
    app = values["apps"]["api-gateway"]
    config = values["config"]
    # Worst case: HPA is one flag away, so count its ceiling whether or not it is on.
    replicas = max(int(app.get("replicas", 1)), int((app.get("hpa") or {}).get("maxReplicas", 0)))
    workers = int(config.get("API_GATEWAY_WORKERS", _dockerfile_workers()))
    per_worker = (int(config.get("API_GATEWAY_DB_POOL_SIZE", _settings_default("API_GATEWAY_DB_POOL_SIZE")))
                  + int(config.get("API_GATEWAY_DB_MAX_OVERFLOW", _settings_default("API_GATEWAY_DB_MAX_OVERFLOW"))))
    assert replicas * workers * per_worker <= _role_limit() - ADMIN_HEADROOM


def test_the_gateway_has_one_pool():
    # A second pool (the asyncpg one this replaced) doubles the footprint the budget counts.
    for root, _dirs, files in os.walk(os.path.join(REPO, "services", "api_gateway")):
        if os.sep + "tests" in root:
            continue
        for name in files:
            if name.endswith(".py"):
                with open(os.path.join(root, name), encoding="utf-8") as f:
                    src = f.read()
                assert "create_pool(" not in src, f"{name} opens a second connection pool"
                assert src.count("create_async_engine(") <= (1 if name == "database.py" else 0), name


@pytest.mark.parametrize("cookie_secure", ["compose", "helm", "settings"])
def test_cookies_are_secure_by_default(cookie_secure):
    # Every edge is HTTPS-only (ADR-0004 dec 1/3): Secure unless a deployment opts out.
    if cookie_secure == "settings":
        assert Settings.model_fields["COOKIE_SECURE"].default is True
    elif cookie_secure == "compose":
        env = yaml.safe_load(_read("docker-compose.yml"))["services"]["api-gateway"]["environment"]
        assert env["COOKIE_SECURE"] == "${COOKIE_SECURE:-true}"
        assert _env_example()["COOKIE_SECURE"] == "true"
    else:
        config = yaml.safe_load(_read("deploy", "helm", "industryflow", "values.yaml"))["config"]
        assert config["COOKIE_SECURE"] == "true"
