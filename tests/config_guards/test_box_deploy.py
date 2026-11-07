# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""scripts/box-deploy.sh deploys with the procedure of the commit it deploys. Bash reads the script it
started from, which the checkout replaces, so without handing over to the new version every change to
the procedure (the backup's place, the revision a build reports) reaches the box one deploy late."""
from _repo import ROOT

DEPLOY = (ROOT / "scripts/box-deploy.sh").read_text()


def test_the_checked_out_version_takes_over_before_anything_is_built():
    checkout = DEPLOY.index('git checkout -q --detach "$ref"')
    handover = DEPLOY.index('BOX_DEPLOY_SHA="$sha" exec scripts/box-deploy.sh "$sha"')
    first_step = min(DEPLOY.index(step) for step in ('echo "== build"', 'echo "== backup"', 'echo "== schema"'))
    assert checkout < handover < first_step
    # Re-run once only: the handed-over run finds its own commit checked out and goes on.
    assert 'if [ "${BOX_DEPLOY_SHA:-}" != "$sha" ]; then' in DEPLOY[checkout:handover]


# --- single-file mounts (the replaced file stays in a running container until it is recreated) ---

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("compose_file_mounts", ROOT / "scripts/compose_file_mounts.py")
mounts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mounts)


def _normalised(compose: dict) -> dict:
    """What `docker compose config --format json` makes of the short volume syntax: absolute bind sources."""
    services = {}
    for name, service in compose["services"].items():
        volumes = []
        for v in service.get("volumes", []):
            source = v.split(":")[0] if isinstance(v, str) else v.get("source", "")
            if source.startswith(("./", "/")):
                volumes.append({"type": "bind", "source": str((ROOT / source).resolve()) if source.startswith(".")
                                else source})
            else:
                volumes.append({"type": "volume", "source": source})
        services[name] = {"volumes": volumes}
    return {"services": services}


def test_a_service_mounting_a_changed_file_is_recreated():
    from _repo import load_yaml
    config = _normalised(load_yaml("docker-compose.yml"))
    changed = {"services/notebook_hub/sso-nginx.conf", "infrastructure/promtail/promtail-config.yml",
               "infrastructure/prometheus/prometheus.yml", "README.md"}
    stale = mounts.stale_services(config, str(ROOT), changed)
    assert {"notebook-sso", "promtail", "prometheus"} <= set(stale), stale
    assert mounts.stale_services(config, str(ROOT), {"README.md"}) == []


def test_a_directory_or_outside_mount_is_not_a_single_file_mount():
    config = {"services": {
        "a": {"volumes": [{"type": "bind", "source": str(ROOT / "infrastructure/prometheus")}]},
        "b": {"volumes": [{"type": "bind", "source": "/var/run/docker.sock"}]},
        "c": {"volumes": [{"type": "volume", "source": "data"}]}}}
    assert mounts.stale_services(config, str(ROOT), {"infrastructure/prometheus", "var/run/docker.sock"}) == []


def test_the_deploy_recreates_what_the_helper_names_among_running_services():
    step = DEPLOY[DEPLOY.index('echo "== single-file configs"'):DEPLOY.index('echo "== monitoring config"')]
    assert 'git diff --name-only "$from" "$sha"' in step
    assert "scripts/compose_file_mounts.py" in step and "--status running" in step
    assert '--force-recreate "${stale[@]}"' in step
