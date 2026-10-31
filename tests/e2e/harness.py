# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The suite's two hands: the public edge, as a signed-in user, and the environment's host."""
from __future__ import annotations

import json
import ssl
import subprocess
import time
from typing import Callable, Iterable, Optional

import httpx

OK = (200, 201, 202, 204)


class Secret:
    """A password the suite holds: its repr is redacted, so a failing test's printed arguments and
    locals never show it."""

    def __init__(self, value: str):
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret(***)"

    __str__ = __repr__


class Session:
    """One user's bearer session at the edge. Each call names the statuses it expects and fails with
    the response body otherwise, so a journey's failure says what the stack answered."""

    def __init__(self, client: httpx.Client, token: str):
        self.client, self.headers = client, {"Authorization": f"Bearer {token}"}

    def request(self, method: str, path: str, *, expect: Iterable[int] = OK, headers=None, **kw) -> httpx.Response:
        r = self.client.request(method, path, headers={**self.headers, **(headers or {})}, **kw)
        if r.status_code not in tuple(expect):
            raise AssertionError(f"{method} {path} -> {r.status_code}, expected {tuple(expect)}: {r.text[:500]}")
        return r

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, **kw):
        return self.request("POST", path, **kw)

    def put(self, path, **kw):
        return self.request("PUT", path, **kw)

    def patch(self, path, **kw):
        return self.request("PATCH", path, **kw)

    def delete(self, path, **kw):
        return self.request("DELETE", path, **kw)


class Edge:
    def __init__(self, base_url: str, ca: str):
        self.client = httpx.Client(base_url=base_url, verify=ssl.create_default_context(cafile=ca), timeout=30.0)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.client.close()

    def login(self, email: str, password: Secret) -> httpx.Response:
        return self.client.post("/auth/jwt/login", data={"username": email, "password": password.reveal()})

    def session(self, email: str, password: Secret) -> Session:
        r = self.login(email, password)
        if r.status_code != 200:
            raise AssertionError(f"sign-in for {email} -> {r.status_code}: {r.text[:300]}")
        return Session(self.client, r.json()["access_token"])

    def anonymous(self, path: str, *, headers=None, method="GET", **kw) -> httpx.Response:
        return self.client.request(method, path, headers=headers or {}, **kw)


class Environment:
    """The deployment's host, through its Docker socket and its own scripts (the compose directory is
    mounted at the host's path, so compose resolves its bind mounts as on the host)."""

    def __init__(self, compose_dir: str):
        self.dir = compose_dir

    def run(self, *argv: str, input: Optional[str] = None, timeout: float = 600) -> str:
        p = subprocess.run(list(argv), cwd=self.dir, input=input, capture_output=True, text=True, timeout=timeout)
        if p.returncode != 0:
            raise AssertionError(f"{' '.join(argv[:4])} … exited {p.returncode}: {p.stderr[-1500:]}")
        return p.stdout

    def sql(self, statement: str) -> str:
        """One statement as the database owner, for what no edge offers; the rows as text."""
        return self.run("docker", "exec", "-i", "industryflow-timescaledb", "psql", "-U", "postgres", "-d",
                        "industryflow", "-v", "ON_ERROR_STOP=1", "-tAq", "-c", statement).strip()

    def redis(self, *args: str) -> str:
        return self.run("docker", "exec", "industryflow-redis", "redis-cli", "--no-raw", *args).strip()

    def job(self, profile: str, service: str, timeout: float = 900) -> str:
        # --no-deps: against the running stack. Starting dependencies re-runs the one-shot steps and
        # recreates TimescaleDB and MinIO behind them, under the live stream.
        return self.run("docker", "compose", "--profile", profile, "run", "--rm", "--no-deps", "-T", service,
                        timeout=timeout)

    def script(self, *argv: str) -> str:
        return self.run("bash", *argv)


def equipment(session: Session, kind: str, name: str, sensors) -> tuple:
    """An equipment with the named sensors, under ids the caller chooses as a client does; returns the
    equipment id and each sensor's id by name."""
    import uuid
    eid = str(uuid.uuid4())
    session.post("/api/equipment", json={"equipment_id": eid, "equipment_type": kind, "name": name,
                                         "sensor_count": len(sensors)})
    ids = {}
    for position, sensor in enumerate(sensors):
        ids[sensor] = str(uuid.uuid4())
        session.post(f"/api/equipment/{eid}/sensors", json={"sensor_id": ids[sensor], "sensor_name": sensor,
                                                              "sensor_type": "process", "position": position})
    return eid, ids


def send_reading(settings, cert, reading) -> httpx.Response:
    """One reading through the device edge, presenting ``cert`` (a (chain, key) pair) or none."""
    context = ssl.create_default_context(cafile=settings["IF_E2E_CA"])
    if cert:
        context.load_cert_chain(*cert)
    with httpx.Client(verify=context, timeout=30.0) as c:
        return c.post(settings["IF_E2E_INGEST"], json=reading)


def reading(equipment_id: str, sensor_id: str, value: float) -> dict:
    from datetime import datetime, timezone
    return {"timestamp": datetime.now(timezone.utc).isoformat(), "sensor_id": sensor_id,
            "equipment_id": equipment_id, "site_id": "e2e", "value": value}


def eventually(check: Callable[[], object], *, timeout: float, every: float = 5.0, what: str = ""):
    """``check()``'s first truthy value within ``timeout`` seconds; the last error or a timeout otherwise."""
    deadline, last = time.monotonic() + timeout, None
    while time.monotonic() < deadline:
        try:
            value = check()
            if value:
                return value
        except AssertionError as exc:
            last = exc
        time.sleep(every)
    raise AssertionError(f"not within {timeout:.0f}s: {what}" + (f" (last: {last})" if last else ""))


def pretty(value) -> str:
    return json.dumps(value, indent=1, default=str)[:800]
