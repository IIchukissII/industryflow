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
    for key, value in {**BASE_ENV, "NOTEBOOK_SPAWNER": profile}.items():
        os.environ[key] = value
    sys.path.insert(0, str(HUB_DIR))
    try:
        cfg = Config()
        namespace = {"c": cfg, "get_config": lambda: cfg, "__file__": str(CONFIG), "__name__": "__main__"}
        exec(compile(CONFIG.read_text(), str(CONFIG), "exec"), namespace)  # noqa: S102
        return cfg
    finally:
        sys.path.remove(str(HUB_DIR))


@pytest.mark.parametrize(
    "profile, spawner_class",
    [("kube", "kubespawner.KubeSpawner"), ("docker", "dockerspawner.DockerSpawner")],
)
def test_both_spawner_profiles_load(profile, spawner_class):
    """ADR-0018 dec 1: the spawner is selected by configuration, so neither profile may rot."""
    cfg = _load(profile)
    assert cfg.JupyterHub.spawner_class == spawner_class


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
