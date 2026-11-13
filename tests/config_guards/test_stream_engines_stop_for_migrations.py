# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""On an upgrade the stream engines stop before the schema sequence and start after it (ADR-0034 dec 11).

A stream engine is a service that holds a Spark checkpoint: in Compose, one whose environment names
CHECKPOINT_LOCATION. The chart finds its engines by image, box-deploy by a hand-kept list; an engine
either of them misses keeps writing while a migration corrects its data, and nothing fails.
"""

from __future__ import annotations

import re

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


def test_the_stop_runs_before_the_backup_and_the_start_after_the_upgrade():
    docs = helm_template()
    hooks = _hooks(docs)
    stop, start = (hooks["Job", "if-industryflow-stream-engines-stop"],
                   hooks["Job", "if-industryflow-stream-engines-start"])
    backup = next(d for d in docs if d["metadata"]["name"].endswith("-pre-upgrade-backup"))
    assert stop["metadata"]["annotations"]["helm.sh/hook"] == "pre-upgrade"
    assert start["metadata"]["annotations"]["helm.sh/hook"] == "post-upgrade"
    weight = lambda d: int(d["metadata"]["annotations"].get("helm.sh/hook-weight", "0"))  # noqa: E731
    assert weight(stop) < weight(backup)
    # The ServiceAccount and its Role exist before either Job runs.
    for kind in ("ServiceAccount", "Role", "RoleBinding"):
        obj = next(d for d in hooks.values() if d["kind"] == kind)
        assert set(obj["metadata"]["annotations"]["helm.sh/hook"].split(",")) == {"pre-upgrade", "post-upgrade"}
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
