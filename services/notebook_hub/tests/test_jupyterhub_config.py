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
    # A test value of the SSO proxy's secret (ADR-0014 dec 3); the hub refuses to start without one.
    "NOTEBOOK_SSO_PROXY_SECRET": "0123456789abcdef0123456789abcdef",
}
PROXY_SECRET = BASE_ENV["NOTEBOOK_SSO_PROXY_SECRET"]


def _load(profile: str, **overrides) -> Config:
    """Execute jupyterhub_config.py exactly as JupyterHub does, and hand back the Config it built."""
    return _load_ns(profile, **overrides)[0]


def _load_ns(profile: str, **overrides):
    """As ``_load``, plus the module namespace the config ran in (its hooks' globals).

    ``overrides`` replace BASE_ENV entries for this load; a value of None removes the variable.
    """
    for key, value in {**BASE_ENV, "NOTEBOOK_SPAWNER": profile, **overrides}.items():
        if value is None:
            os.environ.pop(key, None)
        else:
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
    assert cfg.JupyterHub.spawner_class.__name__.startswith("CapabilityLifecycle")


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
    """Stands in for DockerSpawner/KubeSpawner under the lifecycle subclass; ``running`` is the
    backend (the container or pod)."""

    def __init__(self, spawner):
        self.__dict__.update(spawner.__dict__)
        self.running = True
        self.notified = False

    async def start(self):
        self.running = True

    async def stop(self):
        self.running = False

    async def poll(self):
        return None if self.running else 0

    async def poll_and_notify(self):
        status = await self.poll()
        if status is None:
            return None
        # The hub's path for a server that stopped: route removed, then JupyterHub's User.stop,
        # which stops the backend only while poll() says it runs, and forgets the server.
        self.notified = True
        if await self.poll() is None:
            await self.stop()
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
    assert live.running is False                         # the container itself is gone, not orphaned

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


def _identity_headers(user, company_id, role="engineer", secret=PROXY_SECRET):
    """The headers the SSO proxy forwards: the verified identity plus the proxy's secret."""
    headers = {"X-IF-User": user, "X-IF-Company-Id": company_id, "X-IF-Role": role}
    if secret is not None:
        headers["X-IF-SSO-Proxy-Secret"] = secret
    return headers


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


@pytest.mark.parametrize("secret", [None, "", "wrong-" + PROXY_SECRET, PROXY_SECRET[:-1]])
def test_identity_headers_without_the_proxy_secret_are_not_evidence(secret):
    """A kernel calls the hub API with its token and may add any header. Without the proxy's secret
    those headers neither end the login nor mark it stale (which would stop the user's servers)."""
    user = _User("u-1", str(uuid.uuid4()))
    user.spawners[""] = _Spawner(user)
    headers = {"Authorization": "token abc",
               **_identity_headers("u-1", str(uuid.uuid4()), "operator", secret)}
    ok, handler = _refresh(user, headers)
    assert ok is True and handler.stopped == []


# --------------------------------------------------------------------------------------------------
# The login route honours a forwarded identity only from the SSO proxy (ADR-0014 dec 3). Kernels
# reach the hub API, which is the same application as the login route, so the identity headers
# alone prove nothing; the proxy's secret is what a kernel cannot present.
# --------------------------------------------------------------------------------------------------

class _LoginHandler:
    """What the login handler touches on a real JupyterHub handler."""

    def __init__(self, headers):
        self.request = _Request(headers)
        self.cookie_for = None
        self.redirected = None
        self.logged_in = []

    async def auth_to_user(self, auth):
        self.logged_in.append(auth)
        return _User(auth["name"], auth["auth_state"]["company_id"], auth["auth_state"]["role"])

    def set_login_cookie(self, user):
        self.cookie_for = user

    def get_next_url(self, user):
        return "/jupyter/hub/home"

    def redirect(self, url):
        self.redirected = url


def _login(headers, profile="docker"):
    from tornado import web

    cfg = _load(profile)
    (route, handler_class), = cfg.JupyterHub.authenticator_class().get_handlers(None)
    assert route == r"/login"
    fake = _LoginHandler(headers)
    try:
        asyncio.run(handler_class.get(fake))
    except web.HTTPError as e:
        return e.status_code, fake
    return 200, fake


@pytest.mark.parametrize("profile", ["kube", "docker"])
@pytest.mark.parametrize("secret", [None, "", "x", "wrong-" + PROXY_SECRET, PROXY_SECRET[:-1],
                                    PROXY_SECRET.upper(), PROXY_SECRET + "0"])
def test_a_forged_login_without_the_proxy_secret_is_refused(profile, secret):
    """The cross-tenant escape: a kernel on the hub's network sends another tenant's identity to the
    login route. It must get no session, as anyone."""
    status, fake = _login(_identity_headers("victim", str(uuid.uuid4()), "admin", secret), profile)
    assert status == 401
    assert fake.logged_in == [] and fake.cookie_for is None and fake.redirected is None


@pytest.mark.parametrize("profile", ["kube", "docker"])
def test_the_sso_proxy_login_establishes_the_forwarded_identity(profile):
    cid = str(uuid.uuid4())
    status, fake = _login(_identity_headers("u-1", cid, "engineer"), profile)
    assert status == 200
    assert fake.logged_in == [{"name": "u-1", "auth_state": {"company_id": cid, "role": "engineer"}}]
    assert fake.cookie_for.name == "u-1" and fake.redirected == "/jupyter/hub/home"


def test_the_proxy_secret_does_not_stand_in_for_the_identity():
    status, fake = _login({"X-IF-SSO-Proxy-Secret": PROXY_SECRET})
    assert status == 401 and fake.cookie_for is None


@pytest.mark.parametrize("value", [None, "", "CHANGE_ME_openssl_rand_hex_32", "changeme" + "a" * 32,
                                   "short", "a" * 31, "has space " + "a" * 32, '"$x' + "a" * 32])
def test_the_hub_does_not_start_without_a_usable_proxy_secret(value):
    """Fail closed: a hub that cannot tell the proxy from a kernel must not run at all."""
    try:
        with pytest.raises(ValueError, match="NOTEBOOK_SSO_PROXY_SECRET"):
            _load("docker", NOTEBOOK_SSO_PROXY_SECRET=value)
    finally:
        os.environ["NOTEBOOK_SSO_PROXY_SECRET"] = PROXY_SECRET


@pytest.mark.parametrize("profile", ["kube", "docker"])
def test_the_proxy_secret_never_reaches_a_kernel(profile):
    _, ns, _ = _hub(profile)
    spawner = _Spawner(_User("u-1", str(uuid.uuid4()), role="admin"))
    _spawn(ns, spawner)
    assert "NOTEBOOK_SSO_PROXY_SECRET" not in spawner.environment
    assert PROXY_SECRET not in "".join(map(str, spawner.environment.values()))


@pytest.mark.parametrize("value", ["", "admin,data_scientist", "operator"])
def test_the_hub_does_not_start_with_an_unknown_authoring_role(value):
    """ADR-0014 dec 4: the role-to-profile mapping is configuration, checked against the platform's
    roles; a misspelt role fails the load instead of silently giving its users the other profile."""
    try:
        with pytest.raises(ValueError, match="NOTEBOOK_AUTHORING_ROLES"):
            _load("docker", NOTEBOOK_AUTHORING_ROLES=value)
    finally:
        os.environ.pop("NOTEBOOK_AUTHORING_ROLES", None)


@pytest.mark.parametrize(
    "configured, role, profile",
    [
        (None, "engineer", "authoring"),
        (None, "observer", "analytics"),
        ("admin", "engineer", "analytics"),
        ("admin,observer", "observer", "authoring"),
    ],
)
def test_the_configured_mapping_selects_the_spawn_profile(configured, role, profile):
    try:
        _, ns = _load_ns("docker", NOTEBOOK_AUTHORING_ROLES=configured)
        ns["_STORE"] = FakeStore()
        spawner = _Spawner(_User("u-1", str(uuid.uuid4()), role=role))
        _spawn(ns, spawner)
        assert spawner.environment["INDUSTRYFLOW_PROFILE"] == profile
        assert spawner.extra_create_kwargs["labels"]["industryflow.io/profile"] == profile
    finally:
        os.environ.pop("NOTEBOOK_AUTHORING_ROLES", None)


def test_compose_chp_binds_only_the_platform_network_alias():
    """chp must listen on the alias only the platform network resolves, so the kernel network
    reaches the hub API and not chp. JupyterHub rebuilds its bind URL from ip/port/base_url, which
    once turned a configured host into "all interfaces"; this asserts the URL the app ends up with."""
    from jupyterhub.app import JupyterHub

    try:
        cfg = _load("docker", HUB_PUBLIC_URL="http://notebook-hub-public:8000", NOTEBOOK_BASE_URL="/jupyter/")
        assert JupyterHub(config=cfg).bind_url == "http://notebook-hub-public:8000/jupyter/"
    finally:
        os.environ.pop("HUB_PUBLIC_URL", None)
        os.environ.pop("NOTEBOOK_BASE_URL", None)


def test_kube_profile_drives_the_sibling_chp_and_listens_for_other_pods():
    try:
        cfg = _load("kube", CONFIGPROXY_API_URL="http://chp:8001", HUB_CONNECT_IP="hub-svc")
        assert cfg.JupyterHub.hub_ip == "0.0.0.0"
        assert cfg.JupyterHub.hub_connect_ip == "hub-svc"
        assert cfg.ConfigurableHTTPProxy.should_start is False
        assert cfg.ConfigurableHTTPProxy.api_url == "http://chp:8001"
    finally:
        os.environ.pop("CONFIGPROXY_API_URL", None)
        os.environ.pop("HUB_CONNECT_IP", None)


# --------------------------------------------------------------------------------------------------
# The SSO proxy side: both renderings (compose, Helm) send the secret, and on hub routes only —
# never on /user/ traffic, which reaches single-user servers where user code runs.
# --------------------------------------------------------------------------------------------------

import re  # noqa: E402

REPO = HUB_DIR.parents[1]
SSO_CONFIGS = {
    "compose": (HUB_DIR / "sso-nginx.conf").read_text(),
    "helm": (REPO / "deploy/helm/industryflow/templates/notebook-hub.yaml").read_text(),
}


@pytest.mark.parametrize("where", sorted(SSO_CONFIGS))
def test_the_sso_proxy_always_sets_the_secret_header_it_shares_with_the_hub(where):
    import identity as ident

    conf = SSO_CONFIGS[where]
    # Set unconditionally in the proxied location, so a client-supplied value is always replaced.
    assert re.search(r"proxy_set_header\s+%s\s+\$if_sso_proxy_secret;" % ident.HEADER_PROXY_SECRET, conf)
    assert '"10"    "${%s}";' % ident.PROXY_SECRET_ENV in conf


def _sso_maps(where, base="/jupyter/"):
    conf = SSO_CONFIGS[where]
    hub = re.search(r'map \$request_uri \$if_hub_route \{.*?"~(.*?)" 1;', conf, re.S).group(1)
    hub = hub.replace("${NOTEBOOK_BASE_URL}", base).replace("{{ .Values.notebookHub.baseUrl }}", base)
    ambiguous = re.search(r'map \$request_uri \$if_ambiguous_path \{.*?"~(.*?)" 1;', conf, re.S).group(1)
    # nginx turns \\ into \ inside a quoted string before it compiles the regex.
    return re.compile(hub), re.compile(ambiguous.replace("\\\\", "\\"))


@pytest.mark.parametrize("where", sorted(SSO_CONFIGS))
@pytest.mark.parametrize("uri, sent", [
    ("/jupyter/hub/login?next=%2Fjupyter%2Fhub%2Fhome", True),
    ("/jupyter/hub/home", True),
    ("/jupyter/hub/api/users", True),
    ("/jupyter/user/u-1/lab", False),
    ("/jupyter/user/u-1/hub/login", False),
    ("/jupyter/hub/../user/u-1/lab", False),
    ("/jupyter/hub/%2e%2e/user/u-1/lab", False),
    ("/jupyter/hub/%2E%2E%2Fuser/u-1", False),
    ("/jupyter/hub//user/u-1", False),
    ("/jupyter/hub/..\\user/u-1", False),
    ("/jupyter/hub/x%5Cuser", False),
    ("/jupyter/hub%2Flogin", False),
    ("/hub/login", False),
])
def test_the_secret_is_sent_on_hub_routes_only(where, uri, sent):
    hub, ambiguous = _sso_maps(where)
    assert (bool(hub.search(uri)) and not ambiguous.search(uri)) is sent


# --- reclaim of a deleted tenant's volumes (ADR-0020 dec 6) ---

RECLAIM_ENV = {"NOTEBOOK_RECLAIM_DB_HOST": "timescaledb", "NOTEBOOK_RECLAIM_DB_PASSWORD": "reclaim-secret"}


@pytest.mark.parametrize("profile", ["kube", "docker"])
def test_the_reclaim_service_runs_only_with_its_database_identity(profile):
    try:
        assert "volume-reclaim" not in [s["name"] for s in _load(profile).JupyterHub.services]
        cfg = _load(profile, **RECLAIM_ENV)
    finally:
        for key in RECLAIM_ENV:
            os.environ.pop(key, None)
    [service] = [s for s in cfg.JupyterHub.services if s["name"] == "volume-reclaim"]
    # Its environment is its own settings and how the hub names volumes: none of the hub's other secrets.
    assert service["environment"] == {**RECLAIM_ENV, "NOTEBOOK_RECLAIM_SPAWNER": profile,
                                      "NOTEBOOK_RECLAIM_VOLUME_TEMPLATE": "industryflow-nb-{username}"}
    [role] = [r for r in cfg.JupyterHub.load_roles if r["name"] == "volume-reclaim"]
    assert role["services"] == ["volume-reclaim"] and role["scopes"] == ["delete:users"]


def test_kube_deletes_the_users_claim_by_the_one_name_it_mounts():
    cfg, ns = _load_ns("kube")
    assert cfg.KubeSpawner.pvc_name_template == ns["NB_VOLUME_TEMPLATE"]
    assert cfg.KubeSpawner.delete_pvc is True


def test_docker_removes_the_users_volume_when_the_hub_deletes_the_user():
    import asyncio
    import logging

    from docker.errors import NotFound

    cfg, ns = _load_ns("docker")
    delete_forever = cfg.JupyterHub.spawner_class.delete_forever
    removed = []

    class Spawner:
        log = logging.getLogger("test")
        user = type("U", (), {"name": "u1"})()
        missing = False

        def format_volume_name(self, template, spawner):
            return template.format(username="u1")

        async def docker(self, method, *args, **kwargs):
            if method == "containers":
                return []
            if self.missing:
                raise NotFound("no such volume")
            removed.append((method, *args))

    asyncio.run(delete_forever(Spawner()))
    assert removed == [("remove_volume", ns["NB_VOLUME_TEMPLATE"].format(username="u1"))]
    gone = Spawner()
    gone.missing = True
    asyncio.run(delete_forever(gone))  # an absent volume is already reclaimed


def test_docker_removes_a_container_still_mounting_the_deleted_users_volume():
    """Docker refuses to remove a volume in use (409), and the hub swallows that error."""
    import asyncio
    import logging

    cfg, ns = _load_ns("docker")
    delete_forever = cfg.JupyterHub.spawner_class.delete_forever
    volume = ns["NB_VOLUME_TEMPLATE"].format(username="u1")
    calls = []

    class Spawner:
        log = logging.getLogger("test")
        user = type("U", (), {"name": "u1"})()
        mounted = {"c0ffee000000": volume}

        def format_volume_name(self, template, spawner):
            return template.format(username="u1")

        async def docker(self, method, *args, **kwargs):
            calls.append((method, args, kwargs))
            if method == "containers":
                return [{"Id": cid} for cid, v in self.mounted.items() if v == kwargs["filters"]["volume"]]
            if method == "remove_container":
                self.mounted.pop(args[0])
            if method == "remove_volume" and volume in self.mounted.values():
                raise RuntimeError("409 volume is in use")

    asyncio.run(delete_forever(Spawner()))
    assert [(m, a) for m, a, _ in calls] == [
        ("containers", ()), ("remove_container", ("c0ffee000000",)), ("remove_volume", (volume,))]
    assert calls[1][2] == {"force": True}
