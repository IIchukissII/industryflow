# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
`jupyterhub_config.py` actually loads — under both spawner profiles (ADR-0018).

Nothing in CI stood the hub up, and nothing loaded its config: the other hub tests cover capability
minting and identity, and the image build only proves the packages install. So the file that wires
the whole hub — a custom `Authenticator` subclass, a `BaseHandler`, the spawner class, the
per-session capability hook — was executed for the first time only when a real hub started. A
JupyterHub major could rewrite any of those base classes and every check would stay green.

This loads the real config the way JupyterHub does (a `traitlets` Config plus the `get_config()` the
hub injects) and asserts what the ADRs decided is still wired:

  * ADR-0018 — the spawner is a *deployment profile*, chosen by `NOTEBOOK_SPAWNER`. BOTH profiles
    must load: KubeSpawner (pods) and DockerSpawner (containers).
  * ADR-0012 — the per-session capability handles are minted in the spawner's `pre_spawn_hook`. If a
    major silently dropped the hook, the kernel would spawn with no credentials and the failure would
    surface as a broken notebook, not as a red test.
  * ADR-0014 — the hub has no login of its own; it authenticates from the platform session through
    the header-driven `ProxyHeaderAuthenticator`.

What this does NOT prove is a real spawn. Actually starting a pod needs a cluster, and there isn't
one — that stays cluster-gated. This is the line between "the config still means what it said" and
"the spawn works", and only the first is honestly testable here.
"""
import inspect
import os
import sys
from pathlib import Path

import pytest
from traitlets.config import Config

HUB_DIR = Path(__file__).resolve().parents[1]
CONFIG = HUB_DIR / "jupyterhub_config.py"

# The config reads these at import; they are deployment values, not secrets.
BASE_ENV = {
    "PLATFORM_VERIFY_URL": "http://api-gateway:8000/auth/verify",
    "REDIS_HOST": "localhost",
    "REDIS_PORT": "6379",
    "NOTEBOOK_IMAGE": "industryflow/notebook:test",
    "NOTEBOOK_NETWORK": "industryflow-network",  # DockerSpawner profile requires it
}


def _load(profile: str) -> Config:
    """Execute jupyterhub_config.py exactly as JupyterHub does, and hand back the Config it built."""
    return _load_ns(profile)[0]


def _load_ns(profile: str):
    """As ``_load``, plus the module namespace the config ran in (its hooks' globals)."""
    for key, value in {**BASE_ENV, "NOTEBOOK_SPAWNER": profile}.items():
        os.environ[key] = value
    sys.path.insert(0, str(HUB_DIR))
    try:
        cfg = Config()
        namespace = {"c": cfg, "get_config": lambda: cfg, "__file__": str(CONFIG), "__name__": "__main__"}
        exec(compile(CONFIG.read_text(), str(CONFIG), "exec"), namespace)  # noqa: S102
        return cfg, namespace
    finally:
        sys.path.remove(str(HUB_DIR))


@pytest.mark.parametrize(
    "profile, spawner_class",
    [("kube", "kubespawner.KubeSpawner"), ("docker", "dockerspawner.DockerSpawner")],
)
def test_both_spawner_profiles_load(profile, spawner_class):
    """ADR-0018 dec 1: the spawner is selected by configuration, so neither profile may rot."""
    import importlib

    cfg = _load(profile)
    module, name = spawner_class.rsplit(".", 1)
    base = getattr(importlib.import_module(module), name)
    # The deployment's spawner, carrying the capability lifecycle (ADR-0015 dec 2).
    assert issubclass(cfg.JupyterHub.spawner_class, base)
    assert cfg.JupyterHub.spawner_class.__name__ == f"CapabilityLifecycle{name}"


@pytest.mark.parametrize("profile, section", [("kube", "KubeSpawner"), ("docker", "DockerSpawner")])
def test_capability_minting_hook_is_wired(profile, section):
    """ADR-0012: the kernel's per-session credentials are minted here, or it spawns with none."""
    hook = getattr(_load(profile), section).pre_spawn_hook
    assert hook.__name__ == "bind_identity_and_profile"
    assert inspect.iscoroutinefunction(hook), "the hook awaits the capability store; it must stay async"


@pytest.mark.parametrize("profile", ["kube", "docker"])
def test_hub_authenticates_from_the_platform_session(profile):
    """ADR-0014 dec 1: the hub has no login and no user store of its own."""
    authenticator = _load(profile).JupyterHub.authenticator_class
    assert authenticator.__name__ == "ProxyHeaderAuthenticator"


@pytest.mark.parametrize("profile", ["kube", "docker"])
def test_idle_culler_service_survives(profile):
    """Ephemeral compute (ADR-0020) leans on the culler; idle-culler 2.x must still register."""
    assert "idle-culler" in [s["name"] for s in _load(profile).JupyterHub.services]


@pytest.mark.parametrize("profile", ["kube", "docker"])
def test_hub_migrates_its_own_state_db(profile):
    """
    A hub that meets an older schema than its version expects does not warn — it exits, and the
    container crash-loops. JupyterHub 5 has exactly such a schema, so an in-place upgrade from 4.x
    dies on start unless the hub migrates itself. This trait is the difference between an image an
    operator can roll forward and one that needs an undocumented manual step.
    """
    assert _load(profile).JupyterHub.upgrade_db is True


# --------------------------------------------------------------------------------------------------
# Capability lifecycle (ADR-0015 dec 2, ADR-0012 dec 6): minted per server, refreshed while it is
# healthy, revoked when it stops or is respawned, and the server stopped once they are revoked from
# elsewhere. Driven through the real config's hooks and spawner class with a fake store.
# --------------------------------------------------------------------------------------------------

import asyncio  # noqa: E402
import logging  # noqa: E402
import uuid  # noqa: E402

sys.path.insert(0, str(HUB_DIR / "tests"))
from test_capabilities import FakeStore  # noqa: E402


class _User:
    def __init__(self, name, company_id, role="engineer"):
        self.name = name
        self._state = {"company_id": company_id, "role": role}
        self.spawners = {}

    async def get_auth_state(self):
        return self._state


class _Spawner:
    """What the spawn hook and the lifecycle hooks touch on a real spawner."""

    def __init__(self, user, name=""):
        self.user, self.name = user, name
        self.environment = {}
        self.extra_create_kwargs = {}
        self.extra_labels = {}
        self.log = logging.getLogger("test-spawner")
        self._log_name = f"{user.name}:{name}"
        self.active, self.pending = True, None


def _hub(profile="docker"):
    cfg, ns = _load_ns(profile)
    store = FakeStore()
    ns["_STORE"] = store
    return cfg, ns, store


def _handles(store):
    return {k[len("nbcap:"):] for k in store.data}


def _spawn(ns, spawner):
    asyncio.run(ns["bind_identity_and_profile"](spawner))


@pytest.mark.parametrize("profile, section", [("kube", "KubeSpawner"), ("docker", "DockerSpawner")])
def test_stopping_a_server_revokes_its_handles(profile, section):
    cfg, ns, store = _hub(profile)
    spawner = _Spawner(_User("u-1", str(uuid.uuid4())))
    _spawn(ns, spawner)
    minted = _handles(store)
    assert spawner.environment["INDUSTRYFLOW_API_CAPABILITY"] in minted
    assert spawner.environment["INDUSTRYFLOW_SQL_CAPABILITY"] in minted

    assert cfg.Spawner.post_stop_hook.__name__ == "revoke_server_capabilities"
    asyncio.run(cfg.Spawner.post_stop_hook(spawner))
    assert _handles(store) == set()


def test_a_respawn_revokes_what_the_previous_run_still_held():
    _, ns, store = _hub()
    spawner = _Spawner(_User("u-1", str(uuid.uuid4())))
    _spawn(ns, spawner)
    first = _handles(store)
    _spawn(ns, spawner)  # no stop in between: the hub never saw it (e.g. it crashed)
    assert first and not (first & _handles(store))
    assert _handles(store) == set(spawner.environment[k] for k in (
        "INDUSTRYFLOW_API_CAPABILITY", "INDUSTRYFLOW_SQL_CAPABILITY"))


class _BaseSpawner:
    """Stands in for DockerSpawner/KubeSpawner under the lifecycle subclass."""

    def __init__(self, spawner):
        self.__dict__.update(spawner.__dict__)
        self.running = True
        self.notified = False

    async def start(self):
        self.running = True

    async def poll(self):
        return None if self.running else 0

    async def poll_and_notify(self):
        status = await self.poll()
        if status is None:
            return None
        self.notified = True  # the hub's stop path: route removed, server stopped
        return status


def test_a_healthy_server_keeps_its_handles_alive():
    _, ns, store = _hub()
    spawner = _Spawner(_User("u-1", str(uuid.uuid4())))
    _spawn(ns, spawner)
    for key in list(store.ttls):
        store.ttls[key] = 1  # nearly expired
    live = ns["with_capability_lifecycle"](_BaseSpawner)(spawner)

    assert asyncio.run(live.poll_and_notify()) is None
    assert all(store.ttls[f"nbcap:{h}"] == ns["CAPABILITY_TTL_SECONDS"] for h in _handles(store))
    assert live.notified is False


def test_a_server_whose_handles_were_revoked_elsewhere_is_stopped():
    import capabilities as cap

    _, ns, store = _hub()
    spawner = _Spawner(_User("u-1", str(uuid.uuid4())))
    _spawn(ns, spawner)
    live = ns["with_capability_lifecycle"](_BaseSpawner)(spawner)

    cap.revoke_user(store, "u-1")  # the platform's logout
    assert asyncio.run(live.poll_and_notify()) is None   # noticed...
    assert asyncio.run(live.poll_and_notify()) == 0      # ...and reported stopped at the next poll
    assert live.notified is True

    asyncio.run(live.start())  # a respawn is a fresh server
    assert asyncio.run(live.poll()) is None


def test_a_store_outage_does_not_stop_servers():
    _, ns, store = _hub()
    spawner = _Spawner(_User("u-1", str(uuid.uuid4())))
    _spawn(ns, spawner)
    live = ns["with_capability_lifecycle"](_BaseSpawner)(spawner)

    def down(*a, **k):
        raise ConnectionError("store unreachable")

    store.expire = down
    assert asyncio.run(live.poll_and_notify()) is None
    assert asyncio.run(live.poll_and_notify()) is None
    assert live.notified is False


# --------------------------------------------------------------------------------------------------
# Hub session re-checked against the platform identity (ADR-0014)
# --------------------------------------------------------------------------------------------------

class _Request:
    def __init__(self, headers):
        self.headers = headers


class _Handler:
    def __init__(self, headers):
        self.request = _Request(headers)
        self.stopped = []

    async def stop_single_user(self, user, name):
        self.stopped.append((user.name, name))


def _identity_headers(user, company_id, role="engineer"):
    return {"X-IF-User": user, "X-IF-Company-Id": company_id, "X-IF-Role": role}


@pytest.mark.parametrize("profile", ["kube", "docker"])
def test_hub_login_is_rechecked_often_and_before_spawn(profile):
    cfg = _load(profile)
    assert 0 < cfg.Authenticator.auth_refresh_age <= 300
    assert cfg.Authenticator.refresh_pre_spawn is True
    assert cfg.JupyterHub.shutdown_on_logout is True


def _refresh(user, headers):
    cfg = _load("docker")
    authenticator = cfg.JupyterHub.authenticator_class()
    handler = _Handler(headers)
    return asyncio.run(authenticator.refresh_user(user, handler)), handler


def test_the_same_identity_keeps_the_login():
    cid = str(uuid.uuid4())
    ok, handler = _refresh(_User("u-1", cid), _identity_headers("u-1", cid))
    assert ok is True and handler.stopped == []


def test_another_platform_user_behind_the_cookie_ends_the_login():
    cid = str(uuid.uuid4())
    user = _User("u-1", cid)
    user.spawners[""] = _Spawner(user)
    ok, handler = _refresh(user, _identity_headers("u-2", cid))
    # u-1's server is u-1's, and stays; the cookie no longer speaks for whoever is at the browser.
    assert ok is False and handler.stopped == []


@pytest.mark.parametrize("over", [{"company_id": None}, {"role": "operator"}])
def test_same_user_with_another_tenant_or_role_ends_login_and_stops_servers(over):
    cid = str(uuid.uuid4())
    user = _User("u-1", cid)
    user.spawners[""] = _Spawner(user)
    headers = _identity_headers("u-1", cid)
    if "company_id" in over:
        headers["X-IF-Company-Id"] = str(uuid.uuid4())
    if "role" in over:
        headers["X-IF-Role"] = over["role"]
    ok, handler = _refresh(user, headers)
    assert ok is False and handler.stopped == [("u-1", "")]


def test_token_authenticated_api_calls_are_not_judged_by_headers_they_never_carry():
    ok, handler = _refresh(_User("u-1", str(uuid.uuid4())), {"Authorization": "token abc"})
    assert ok is True and handler.stopped == []
