<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0009: Deployment and orchestration — Kubernetes, packaged with Helm

- **ID:** ADR-0009
- **Status:** Accepted
- **Date:** 2026-06-26 (accepted 2026-06-27)
- **Project:** IndustryFlow
- **Parent:** ADR-0001
- **Companions:** ADR-0002 (ingestion mTLS edge), ADR-0004 (API HTTPS edge), ADR-0006 (durable Spark checkpoints)

## Context and problem

IndustryFlow ships today as a single `docker-compose.yml` of ~20 containers. Compose is a fine local-development tool, but it cannot deliver what ADR-0001 commits the project to: a **commercial-managed** offering running alongside **self-hosted** deployments. Compose has no rolling updates, no horizontal autoscaling, no self-healing, no multi-node scheduling, and no standard story for resource limits (the review found none set) or for the TLS edge that ADR-0002 (device mTLS) and ADR-0004 (browser HTTPS) both assume. A managed multi-tenant service needs an orchestrator; the natural and ubiquitous one is Kubernetes.

Moving to Kubernetes raises immediate questions the project has not decided: how the manifests are packaged and parameterized per environment; how the stateful infrastructure (TimescaleDB, Kafka, Redis, MinIO, MLflow) is run when the same chart must serve both a self-hoster who wants everything in-cluster and a managed deployment that points at external managed datastores; and where the TLS edge lives. This ADR records those decisions. It does not retire `docker-compose.yml` as a local-dev convenience; it adds the deployment path the managed/self-hosted split requires.

## Decision drivers

- **ADR-0001 requires both self-hosted and managed deployments from one architecture.** The packaging must serve a self-hoster running everything in-cluster and a managed operator using external datastores, without forking.
- **A managed service needs orchestration primitives compose lacks.** Rolling updates, autoscaling, self-healing, scheduling, and resource governance are the point of moving.
- **The TLS edge already has a home in the ADRs.** ADR-0002's mTLS reverse proxy and ADR-0004's HTTPS termination map onto a Kubernetes Ingress; the move should consolidate them there.
- **Stateful and stateless workloads have different lifecycles.** Services scale and roll freely; datastores need stable identity and storage. The packaging must distinguish them.
- **Secrets must not live in the chart.** Database passwords and the JWT secret are injected, not committed.

## Decision

1. **Kubernetes is the deployment target; Helm is the packaging.** A single Helm chart (`deploy/helm/industryflow`) renders the manifests, parameterized by `values.yaml` per environment. Helm is chosen over raw manifests/Kustomize for its templating and, decisively, its ability to toggle optional dependencies (decision 3).

2. **Stateless workloads are Deployments; the Spark jobs are Deployments too.** The application services (api-gateway, ml-service API, alert-service API, alert-service detector worker, ingestion-service) and the Spark streaming/aggregation jobs run as Deployments with liveness/readiness probes and resource requests and limits on every workload — closing the review's "no resource limits" gap. They are horizontally scalable (HPA-ready) where the workload allows.

3. **Stateful infrastructure runs as StatefulSets, gated by a per-dependency `inCluster` toggle.** TimescaleDB, Kafka, Zookeeper, Redis, MinIO, and MLflow are StatefulSets with PersistentVolumeClaims when `inCluster` is true (the self-hosted default), or are omitted in favour of an externally-supplied endpoint when false (the managed default). One chart serves both deployment models from ADR-0001 by flipping toggles, with no fork.

4. **Configuration is a ConfigMap; secrets are a Kubernetes Secret injected externally.** Non-secret configuration (hosts, ports, topic names) is a ConfigMap; secrets (DB passwords, JWT secret, MinIO keys) are a Secret whose values are supplied by a secrets backend (SealedSecrets/Vault/CSI) in real deployments and are never committed to the chart. The chart ships placeholders, not values.

5. **A single Ingress is the TLS edge.** The Ingress terminates HTTPS for the API and frontend with a publicly-trusted certificate (ADR-0004) and routes the device-ingestion path through the mTLS termination decided in ADR-0002. The reverse proxy those ADRs describe is realized as the Ingress (and its controller), not as per-service TLS.

6. **`docker-compose.yml` remains the local-development path; Helm is for shared and managed deployments.** The two are not kept in lockstep feature-for-feature; compose is the developer convenience, Helm is the deployment artifact. Where a value is authoritative it lives in one place per ADR-0000 (e.g. topic names in configuration consumed by both), not copied between them.

## Alternatives considered

**A. Keep docker-compose as the only deployment mechanism.** *Rejected:* it cannot provide rolling updates, autoscaling, self-healing, or multi-node scheduling, and has no managed-multi-tenant story — exactly what ADR-0001 commits to. Compose stays for local dev (decision 6), not as the deployment path.

**B. Package with raw manifests or Kustomize instead of Helm.** *Rejected:* raw manifests cannot parameterize per environment, and Kustomize overlays express environment differences but not cleanly the *optional in-cluster-vs-external dependency* toggle that decision 3 needs. Helm's conditionals and values fit the self-hosted/managed split directly.

**C. Always run stateful infrastructure in-cluster.** *Rejected:* a managed cloud deployment wants managed Postgres/Kafka/object-store for their durability and operational guarantees; forcing in-cluster StatefulSets there is a step backwards. The toggle (decision 3) keeps in-cluster as the self-host default without imposing it on managed.

**D. Always require external managed datastores.** *Rejected:* a self-hoster must be able to run the whole stack on their own cluster with no external dependencies. Again the toggle serves both.

**E. Use dedicated operators for the stateful services (e.g. a Postgres/Kafka operator).** *Deferred, not rejected:* operators run stateful services better than hand-written StatefulSets at scale and are the likely future direction, but they add heavy dependencies and are more than this first pass needs. StatefulSets now; operators revisited when operational load justifies them.

## Consequences

### Positive

- The managed offering becomes buildable: rolling updates, autoscaling, self-healing, and resource governance are available, and the same chart serves self-hosted and managed by toggling dependencies.
- The TLS edge from ADR-0002 and ADR-0004 has a single concrete home (the Ingress), rather than being an unrealized "reverse proxy" in the prose.
- Every workload gets resource requests/limits and health probes, closing concrete review gaps.
- Durable storage for stateful services and Spark checkpoints (ADR-0006) is expressed as PVCs rather than ephemeral container paths.

### Negative

- Kubernetes adds real operational complexity over compose: a cluster, an ingress controller, storage classes, and a secrets backend must exist and be operated.
- Running Kafka and Postgres well as hand-written StatefulSets is non-trivial; alternative E (operators) may become necessary, and until then the in-cluster path carries that operational risk.
- Secrets management must be solved properly (decision 4); a chart that ships placeholder secrets is not deployable until a backend supplies real ones.
- There are now two deployment descriptions (compose for dev, Helm for deploy); decision 6 and ADR-0000 bound the drift, but the surface exists.

## Deferred decisions

- **Monitoring stack.** The Prometheus/Grafana/Loki/exporters chart is a later pass (this scope is app + core data services).
- **Stateful operators (alternative E).** Whether and when to adopt Postgres/Kafka operators instead of StatefulSets.
- **Autoscaling policy.** HPA metrics, thresholds, and which workloads scale.
- **Secrets backend.** SealedSecrets vs Vault vs CSI driver for injecting the Secret's values.
- **Spark on Kubernetes.** Whether the Spark jobs stay as plain Deployments or move to native spark-on-k8s submission.

## Resolved since acceptance

The chart at acceptance realizes decisions that were placeholders in the first pass:

- **Frontend.** It now has a Dockerfile (the TLS edge of ADR-0004) and is **enabled** in the
  chart, served behind the Ingress.
- **mTLS device-ingestion edge (decision 5).** Realized as a dedicated Ingress whose
  ingress-nginx controller verifies device client certificates against the device CA
  (ADR-0007) and passes the verified certificate to `ingestion-service` — the same identity
  the service reads in the compose path.
- **NetworkPolicy (ADR-0002 decision 3).** A toggleable NetworkPolicy restricts
  `ingestion-service` ingress to the cluster TLS edge, so the verified-identity headers
  cannot be spoofed by another pod.
- **Image registry and digest pinning.** Images are built, scanned (Trivy, gating on fixable
  CRITICAL), and published to `ghcr.io/iichukissii` by `.github/workflows/images.yml`
  (`:latest` + `:sha-<short>`). The chart references images by **immutable digest** in
  production: third-party images (TimescaleDB, Kafka/ZooKeeper, Redis, MinIO, the notebook
  proxy/nginx) are pinned as `name:tag@sha256:…` directly in `values.yaml`; first-party
  `industryflow-*` images carry a per-image `digest:` (empty by default → the mutable
  `image.tag` for dev) that, when set, wins over the tag via the `industryflow.image` helper.
  Because first-party digests rotate per build, the production overlay is *generated*, not
  hand-maintained: `scripts/pin-image-digests.sh <tag> > values-digests.yaml`, applied with
  `-f`. This resolves the original "no pinned images" review concern.

## References

- ADR-0001 — framing; the self-hosted + commercial-managed split this chart's toggles serve.
- ADR-0002 — ingestion mTLS; the device-auth edge realized as the Ingress.
- ADR-0004 — API HTTPS; the browser-facing TLS termination realized as the Ingress.
- ADR-0006 — Spark windowing; durable checkpoints realized as PVCs.
- ADR-0000 — single source of truth; bounds the compose/Helm duplication.
