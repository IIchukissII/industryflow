<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0009 — Kubernetes deployment packaged with Helm

- **Status:** Accepted
- **Date:** 2026-06-27; changed 2026-10-31
- **Parent:** ADR-0001
- **Related:** ADR-0002 and ADR-0004 (the TLS edges), ADR-0006 (Spark checkpoints), ADR-0016
  (monitoring), ADR-0018 (single-host Compose)

## Context

Scaled self-hosting and the managed offering (ADR-0001) need rolling updates, autoscaling,
self-healing and multi-node scheduling. One package must serve in-cluster and external datastores
without a fork, and must never contain secrets.

## Decision

1. **Kubernetes is the target for scaled and managed deployments; one Helm chart is the packaging.**
   Helm is chosen for templating and per-dependency toggles.
2. **Stateless workloads, including the Spark jobs and the tracking server, are Deployments with
   probes and resource bounds.** A job with a single-writer checkpoint volume is replaced, never rolled.
3. **Stateful infrastructure is a StatefulSet behind a per-dependency in-cluster toggle.** The
   database, broker, coordinator, key-value store and object store run in-cluster by default for
   self-hosting, or point at external endpoints for managed.
4. **Configuration is a ConfigMap; secrets are injected from outside the chart.** The chart refuses
   placeholder secrets and gives each workload only the keys it uses.
5. **The cluster ingress is the TLS edge.** One controller terminates browser HTTPS (ADR-0004 dec 8)
   and verifies device certificates (ADR-0007), and a network policy admits only it to ingestion.
6. **Single-host Compose is the development path and a supported single-host deployment; Helm serves
   scale.** The two are not in feature lockstep, and a shared value has one home (ADR-0000 dec 3).

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| 1 | Compose only | No rolling updates, autoscaling or multi-node scheduling |
| 1 | Raw manifests or Kustomize | No clean in-cluster-or-external toggle |
| 2 | A separate alerting HTTP service among the Deployments | Retired by ADR-0031 dec 2 |
| 2 | Tracking server as a StatefulSet | Its state is in the database and object store |
| 3 | Datastores always in-cluster | A managed deployment loses the durability of managed datastores |
| 3 | Datastores always external | A self-hoster cannot run the whole stack on their own cluster |
| 4 | Placeholder secrets shipped in the chart | A deployment that forgets to replace them runs on known credentials |
| 4 | One Secret for every workload | Every pod would read every credential |
| 6 | Compose as a development convenience only, never a deployment | Single-host self-hosting is a real, supported shape |

## Consequences

**Positive**
- One chart serves both deployment models, with one TLS edge and bounded, probed workloads.
- Placeholder secrets cannot be deployed by accident.

**Negative**
- A cluster, ingress controller, storage classes and secrets backend must be operated.
- Hand-written StatefulSets for the database and broker carry operational risk.
- Two deployment descriptions exist side by side.

## Deferred

| Open question | Settled by |
|---|---|
| Operators instead of StatefulSets | Operational load of in-cluster datastores |
| Autoscaling policy (workloads, metrics, thresholds) | First load test against the chart |
| Secrets backend | First production cluster |
| Native Spark-on-Kubernetes submission | A Spark job needing more than one process |
| The chart proven on a live cluster: per-user notebook pods, network-policy enforcement, non-root stateful infrastructure with volume ownership | The first IndustryFlow cluster |

## References

- [deploy/helm/industryflow/README.md](../deploy/helm/industryflow/README.md);
  [docs/operations/high-availability.md](../docs/operations/high-availability.md).
