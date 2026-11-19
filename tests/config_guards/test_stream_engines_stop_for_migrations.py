# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""On an upgrade the stream engines stop before the schema sequence and start after it, and an upgrade that
fails starts them again (ADR-0034 dec 11).

A stream engine is a service that holds a Spark checkpoint: in Compose, one whose environment names
CHECKPOINT_LOCATION. The chart finds its engines by image, box-deploy by a hand-kept list; an engine
either of them misses keeps writing while a migration corrects its data, and nothing fails.
"""

from __future__ import annotations

import os
import re
import subprocess

from _repo import ROOT, helm_template, load_yaml


def _compose_engines() -> set[str]:
    services = load_yaml("docker-compose.yml")["services"]
    engines = {name for name, svc in services.items() if "CHECKPOINT_LOCATION" in str(svc.get("environment"))}
    assert engines, "no Compose service holds a checkpoint: the guard is not looking"
    return engines


def _hooks(docs):
    return {(d["kind"], d["metadata"]["name"]): d for d in docs if "stream-engines" in d["metadata"]["name"]}


def test_the_chart_stops_every_stream_engine_and_only_them():
    docs = helm_template()
    role = next(d for d in docs if d["kind"] == "Role" and d["metadata"]["name"].endswith("-stream-engines"))
    scaled = set(next(r for r in role["rules"] if "deployments/scale" in r["resources"])["resourceNames"])
    assert scaled == {f"if-industryflow-{name}" for name in _compose_engines()}


def test_the_stop_runs_after_the_backup_and_the_start_after_the_upgrade_or_a_rollback():
    docs = helm_template()
    hooks = _hooks(docs)
    stop, start = (hooks["Job", "if-industryflow-stream-engines-stop"],
                   hooks["Job", "if-industryflow-stream-engines-start"])
    backup = next(d for d in docs if d["metadata"]["name"].endswith("-pre-upgrade-backup"))
    assert stop["metadata"]["annotations"]["helm.sh/hook"] == "pre-upgrade"
    # A rolled-back upgrade starts the engines again (ADR-0034 dec 11).
    assert set(start["metadata"]["annotations"]["helm.sh/hook"].split(",")) == {"post-upgrade", "post-rollback"}
    weight = lambda d: int(d["metadata"]["annotations"].get("helm.sh/hook-weight", "0"))  # noqa: E731
    # The last step before the release: a failed backup stops nothing.
    assert weight(stop) > weight(backup)
    assert all(weight(d) <= weight(stop) for d in docs
               if "pre-upgrade" in (d["metadata"].get("annotations") or {}).get("helm.sh/hook", ""))
    # The ServiceAccount and its Role exist before either Job runs, a rollback's included.
    for kind in ("ServiceAccount", "Role", "RoleBinding"):
        obj = next(d for d in hooks.values() if d["kind"] == kind)
        assert set(obj["metadata"]["annotations"]["helm.sh/hook"].split(",")) == \
            {"pre-upgrade", "post-upgrade", "post-rollback"}
        assert weight(obj) < weight(stop)
    # The migrate Job is an ordinary resource, applied only after every pre-upgrade hook.
    migrate = next(d for d in docs if d["kind"] == "Job" and "-db-migrate-" in d["metadata"]["name"])
    assert "helm.sh/hook" not in (migrate["metadata"].get("annotations") or {})


def test_the_start_restores_each_engine_to_its_declared_replicas():
    values = load_yaml("deploy/helm/industryflow/values.yaml")["apps"]
    script = _hooks(helm_template())["Job", "if-industryflow-stream-engines-start"][
        "spec"]["template"]["spec"]["containers"][0]["args"][0]
    started = dict(re.findall(r"deployment/if-industryflow-(\S+) --replicas=(\d+)", script))
    assert started == {name: str(values[name].get("replicas", 1)) for name in _compose_engines()}


_FAKE_KUBECTL = """#!/bin/sh
echo "$*" >> "$KUBECTL_LOG"
case "$*" in
  "get deployment "*" --ignore-not-found -o name") echo "deployment.apps/$3" ;;
  "get deployment.apps/"*" -o jsonpath="*) echo 3 ;;
  "get pods "*) [ -n "$PODS_STAY" ] && echo pod/engine-0 ;;
esac
exit 0
"""


def _run_stop(tmp_path, *, pods_stay: bool) -> tuple[int, list[str]]:
    """Run the rendered stop script against a kubectl that records each call."""
    script = _hooks(helm_template("--set", "dbMigrations.streamEngines.stopTimeoutSeconds=0"))[
        "Job", "if-industryflow-stream-engines-stop"]["spec"]["template"]["spec"]["containers"][0]["args"][0]
    (tmp_path / "kubectl").write_text(_FAKE_KUBECTL)
    (tmp_path / "kubectl").chmod(0o755)
    log = tmp_path / "calls"
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "KUBECTL_LOG": str(log),
           "PODS_STAY": "1" if pods_stay else ""}
    rc = subprocess.run(["/bin/sh", "-euc", script], env=env, capture_output=True, text=True).returncode
    return rc, log.read_text().splitlines()


def test_a_stop_that_fails_starts_the_engines_again_at_their_replicas(tmp_path):
    """ADR-0034 dec 11: a failure never leaves the engines stopped; the stop's own failure, a timeout here."""
    rc, calls = _run_stop(tmp_path, pods_stay=True)
    assert rc != 0
    engines = {f"deployment.apps/if-industryflow-{name}" for name in _compose_engines()}
    restored = {c.split()[1] for c in calls if c.startswith("scale deployment.apps/") and c.endswith("--replicas=3")}
    assert restored == engines
    assert calls.index(next(c for c in calls if c.endswith("--replicas=0"))) < \
        calls.index(next(c for c in calls if c.endswith("--replicas=3")))


def test_a_stop_that_succeeds_starts_nothing(tmp_path):
    rc, calls = _run_stop(tmp_path, pods_stay=False)
    assert rc == 0
    assert not any(c.endswith("--replicas=3") for c in calls)


def _selects(selector: dict, labels: dict) -> bool:
    return all(labels.get(k) == v for k, v in (selector.get("matchLabels") or {}).items()) and not \
        selector.get("matchExpressions")


def test_the_stop_waits_on_the_labels_the_engines_pods_carry():
    # A selector that matches no pod ends the wait at once, while the engines still drain.
    docs = helm_template()
    script = _hooks(docs)["Job", "if-industryflow-stream-engines-stop"][
        "spec"]["template"]["spec"]["containers"][0]["args"][0]
    selector = re.search(r"selector='([^']*)'", script).group(1)
    instance = re.search(r"app\.kubernetes\.io/instance=([^,]+)", selector).group(1)
    names = set(re.search(r"app\.kubernetes\.io/name in \(([^)]*)\)", selector).group(1).split(","))
    assert names == _compose_engines()
    for name in names:
        deployment = next(d for d in docs if d["kind"] == "Deployment"
                          and d["metadata"]["name"] == f"if-industryflow-{name}")
        labels = deployment["spec"]["template"]["metadata"]["labels"]
        assert labels["app.kubernetes.io/name"] == name and labels["app.kubernetes.io/instance"] == instance


def test_no_egress_policy_cuts_the_hooks_off_the_api_server():
    docs = helm_template()
    for (kind, _), job in _hooks(docs).items():
        if kind != "Job":
            continue
        labels = job["spec"]["template"]["metadata"]["labels"]
        for policy in (d for d in docs if d["kind"] == "NetworkPolicy"):
            if "Egress" in policy["spec"].get("policyTypes", []):
                assert not _selects(policy["spec"]["podSelector"], labels), policy["metadata"]["name"]


def test_box_deploy_stops_every_stream_engine_before_the_schema_sequence():
    text = (ROOT / "scripts" / "box-deploy.sh").read_text()
    spark = set(re.search(r"^SPARK=\(([^)]*)\)", text, re.M).group(1).split())
    assert _compose_engines() <= spark
    assert text.index('docker compose stop "${SPARK[@]}"') < text.index("--exit-code-from db-migrate")
