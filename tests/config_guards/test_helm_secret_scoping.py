# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Each workload gets only the secret keys it uses, whoever supplies the Secret (ADR-0009 dec 4).

A Secret taken by `envFrom` hands the pod every key in it. The chart's own scoped Secrets hide that,
but with `secrets.existingSecret` every workload names the operator's one Secret, and `envFrom` then
gives each pod every credential of the platform. So no workload takes a Secret by `envFrom`; each
key is a `secretKeyRef`, and a workload reads the same keys in both modes.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest
from _repo import CHART, helm_template

EXISTING = "operator-secret"

# Every optional plane that carries a credential, so every workload is in the render.
ALL_ON = [
    "--set", "notebookHub.enabled=true",
    "--set", "ingress.mtls.enabled=true",
    "--set", "observability.opsAgent.enabled=true",
    "--set", "secrets.data.OPS_AGENT_WEBHOOK_SECRET=guard-only-webhook-secret-of-32-chars-or-more",
    "--set", "observability.opsAgent.prometheusUrl=http://prometheus.monitoring:9090",
    "--set", "observability.opsAgent.lokiUrl=http://loki.monitoring:3100",
    "--set", "observability.opsAgent.alertmanagerUrl=http://alertmanager.monitoring:9093",
]
MODES = {
    "chart-secrets": ALL_ON,
    "existing-secret": [*ALL_ON, "--set", "secrets.create=false", "--set", f"secrets.existingSecret={EXISTING}"],
}


def _pod_spec(doc: dict) -> dict | None:
    kind = doc.get("kind")
    if kind in ("Deployment", "StatefulSet", "Job"):
        return doc["spec"]["template"]["spec"]
    if kind == "CronJob":
        return doc["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    return None


def _workloads(args) -> dict[tuple[str, str], list[dict]]:
    out = {}
    for doc in helm_template(*args):
        spec = _pod_spec(doc)
        if spec is not None:
            out[(doc["kind"], doc["metadata"]["name"])] = spec.get("initContainers", []) + spec["containers"]
    return out


def _secret_refs(containers) -> set[tuple[str, str]]:
    return {
        (e["valueFrom"]["secretKeyRef"]["name"], e["valueFrom"]["secretKeyRef"]["key"])
        for c in containers for e in c.get("env", [])
        if "secretKeyRef" in e.get("valueFrom", {})
    }


@pytest.mark.parametrize("mode", MODES)
def test_no_workload_takes_a_whole_secret(mode):
    offenders = {
        f"{kind}/{name}:{c['name']}"
        for (kind, name), containers in _workloads(MODES[mode]).items()
        for c in containers for src in c.get("envFrom", []) if "secretRef" in src
    }
    assert not offenders, f"[{mode}] a Secret by envFrom hands the pod every key in it: {sorted(offenders)}"


def test_every_key_a_workload_names_is_in_its_scoped_secret():
    docs = helm_template(*MODES["chart-secrets"])
    secrets = {d["metadata"]["name"]: set(d.get("stringData", {})) for d in docs if d.get("kind") == "Secret"}
    missing = set()
    for (kind, name), containers in _workloads(MODES["chart-secrets"]).items():
        for secret, key in _secret_refs(containers):
            if secret.endswith("-secrets") and key not in secrets.get(secret, set()):
                missing.add(f"{kind}/{name}: {secret}[{key}]")
    assert not missing, f"keys named but absent from the chart's Secret: {sorted(missing)}"


def test_an_existing_secret_hands_each_workload_the_same_keys():
    scoped = _workloads(MODES["chart-secrets"])
    shared = _workloads(MODES["existing-secret"])
    assert scoped.keys() == shared.keys()
    checked = 0
    for workload, containers in scoped.items():
        chart_keys = {key for secret, key in _secret_refs(containers) if secret.endswith("-secrets")}
        refs = _secret_refs(shared[workload])
        existing_keys = {key for secret, key in refs if secret == EXISTING}
        assert not {s for s, _ in refs if s.endswith("-secrets")}, f"{workload} names a chart Secret"
        assert existing_keys == chart_keys, (
            f"{workload}: with existingSecret it reads {sorted(existing_keys - chart_keys)} more, "
            f"{sorted(chart_keys - existing_keys)} fewer than its scoped Secret"
        )
        checked += bool(chart_keys)
    assert checked >= 10, "too few workloads read a secret: the guard is not looking"


def test_the_chart_refuses_its_own_placeholder_secrets():
    helm = shutil.which("helm")
    assert helm, "helm is required for the chart guards"
    refused = subprocess.run([helm, "template", "if", str(CHART)], capture_output=True, text=True)
    assert refused.returncode != 0 and "is still a placeholder" in refused.stderr
    # An existing Secret leaves the chart no secret value to render, and so none to refuse.
    accepted = subprocess.run(
        [helm, "template", "if", str(CHART), "--set", "secrets.create=false",
         "--set", f"secrets.existingSecret={EXISTING}"], capture_output=True, text=True,
    )
    assert accepted.returncode == 0, accepted.stderr
