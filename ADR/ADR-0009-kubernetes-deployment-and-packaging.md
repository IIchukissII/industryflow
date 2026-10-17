<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0009: Deployment and orchestration — Kubernetes, packaged with Helm (rev 2)

- **ID:** ADR-0009
- **Status:** Accepted
- **Date:** 2026-06-27 (rev 1: 2026-06-28; rev 2: 2026-10-13)
- **Project:** IndustryFlow
- **Parent:** ADR-0001 (framing)
- **Companions:** ADR-0002 (ingestion mTLS edge), ADR-0004 (API HTTPS edge), ADR-0006 (durable Spark checkpoints)
- **Related:** ADR-0018 (spawner portability — establishes single-host Compose as a first-class self-hosted target), ADR-0016 (observability stack deferred to here), ADR-0031 (retires the separate alerting API listed in decision 2 at rev 1)
- **Supersedes:** ADR-0009 rev 0 (2026-06-27 — treated `docker-compose.yml` as a local-development convenience only and rejected Compose as a deployment path; rev 1 admits single-host Compose as a supported self-hosted deployment target, reconciling this ADR with ADR-0018); rev 1 (2026-06-28 — listed a separate alerting API among the Deployments and the tracking server among the StatefulSets; rev 2 follows ADR-0031 for the first and classifies the tracking server by where its state lives)

## Context and problem

IndustryFlow ships today as a single `docker-compose.yml` of ~20 containers. Compose is a fine local-development tool, but it cannot deliver what ADR-0001 commits the project to: a **commercial-managed** offering running alongside **self-hosted** deployments. Compose has no rolling updates, no horizontal autoscaling, no self-healing, no multi-node scheduling, and no standard story for resource limits (the review found none set) or for the TLS edge that ADR-0002 (device mTLS) and ADR-0004 (browser HTTPS) both assume. A managed multi-tenant service needs an orchestrator; the natural and ubiquitous one is Kubernetes.

Moving to Kubernetes raises immediate questions the project has not decided: how the manifests are packaged and parameterized per environment; how the stateful infrastructure (TimescaleDB, Kafka, Redis, MinIO, MLflow) is run when the same chart must serve both a self-hoster who wants everything in-cluster and a managed deployment that points at external managed datastores; and where the TLS edge lives. This ADR records those decisions. It does not retire `docker-compose.yml`; it adds the Kubernetes deployment path the *managed* offering and *scaled* self-hosting require.

**Revision 1 (2026-06-28).** Rev 0 framed Compose as a local-development convenience only and rejected it as a deployment path, with self-hosting equated to in-cluster Helm. That was too strong: a small self-hoster running a single host is a real, supported deployment shape, and the platform in fact runs that way today (the notebook hub's Compose spawner in ADR-0018 is built on this premise, and ADR-0017 records "the live deployment is docker-compose"). Rev 1 therefore recognizes **two self-hosted shapes** — single-host Compose for small/test deployments, and in-cluster Helm for scaled self-hosting — while the *managed* offering remains Kubernetes/Helm. Kubernetes is still the target for everything Compose structurally cannot do (rolling updates, autoscaling, self-healing, multi-node scheduling, managed-multi-tenancy); what changes is that Compose is no longer *only* a dev tool. Decision 6 and Alternative A below carry the revised stance.

**Revision 2 (2026-10-13).** Two entries in the workload lists had stopped being true. Decision 2 still listed an alerting-service API among the stateless Deployments after ADR-0031 retired that deployable — the alerting service now has no HTTP surface of its own. And decision 3 listed the experiment-tracking server among the StatefulSets, while the chart has always run it as a Deployment. The chart is right and the record was wrong, by the record's own driver: what makes a workload stateful is that it owns durable storage and a stable identity. The tracking server owns neither — its metadata lives in the database and its artifacts in the object store, both of which are toggled and run under decision 3 — so it is a stateless front to stateful stores, and scales and rolls like any other service. Rev 2 corrects both lists and marks the first as ADR-0031's.

## Decision drivers

- **ADR-0001 requires both self-hosted and managed deployments from one architecture.** The packaging must serve a self-hoster running everything in-cluster and a managed operator using external datastores, without forking.
- **A managed service needs orchestration primitives compose lacks.** Rolling updates, autoscaling, self-healing, scheduling, and resource governance are the point of moving.
- **The TLS edge already has a home in the ADRs.** ADR-0002's mTLS reverse proxy and ADR-0004's HTTPS termination map onto a Kubernetes Ingress; the move should consolidate them there.
- **Stateful and stateless workloads have different lifecycles.** Services scale and roll freely; datastores need stable identity and storage. The packaging must distinguish them.
- **Secrets must not live in the chart.** Database passwords and the JWT secret are injected, not committed.

## Decision

1. **Kubernetes is the deployment target; Helm is the packaging.** A single Helm chart (`deploy/helm/industryflow`) renders the manifests, parameterized by `values.yaml` per environment. Helm is chosen over raw manifests/Kustomize for its templating and, decisively, its ability to toggle optional dependencies (decision 3).

2. **Stateless workloads are Deployments; the Spark jobs are Deployments too.** The application services (api-gateway, ml-service API, the alert-service detector worker, ingestion-service, and the experiment-tracking server — rev 2) and the Spark streaming/aggregation jobs run as Deployments with liveness/readiness probes and resource requests and limits on every workload — closing the review's "no resource limits" gap. They are horizontally scalable (HPA-ready) where the workload allows.

   > *Rev 1 read:* "(api-gateway, ml-service API, alert-service API, alert-service detector worker, ingestion-service)". The alert-service API is retired by ADR-0031 dec 2; the tracking server moves here from decision 3 (rev 2).

3. **Stateful infrastructure runs as StatefulSets, gated by a per-dependency in-cluster toggle.** The workloads that own durable storage — the database, the message broker and its coordinator, the key-value store and the object store — are StatefulSets with persistent volume claims when run in-cluster (the self-hosted default), or are omitted in favour of an externally-supplied endpoint (the managed default). The experiment-tracking server sits behind the same toggle but is a Deployment (decision 2): its state lives in the database and the object store, not in the pod *(rev 2)*. One chart serves both deployment models from ADR-0001 by flipping toggles, with no fork.

   > *Rev 1 read:* "TimescaleDB, Kafka, Zookeeper, Redis, MinIO, and MLflow are StatefulSets". The chart never ran the tracking server that way, and by this decision's own criterion it should not.

4. **Configuration is a ConfigMap; secrets are a Kubernetes Secret injected externally.** Non-secret configuration (hosts, ports, topic names) is a ConfigMap; secrets (DB passwords, JWT secret, MinIO keys) are a Secret whose values are supplied by a secrets backend (SealedSecrets/Vault/CSI) in real deployments and are never committed to the chart. The chart ships placeholders, not values.

5. **A single Ingress is the TLS edge.** The Ingress terminates HTTPS for the API and frontend with the edge certificate of ADR-0004 dec 8 — a publicly-trusted ACME certificate for the managed deployment, or an internal CA (cert-manager) for in-cluster self-hosting — and routes the device-ingestion path through the mTLS termination decided in ADR-0002. The reverse proxy those ADRs describe is realized as the Ingress (and its controller), not as per-service TLS.

6. **`docker-compose.yml` is the local-development path *and* a supported single-host self-hosted deployment; Helm/Kubernetes is for scaled self-hosting and the managed offering.** *(rev 1 — rev 0 read "compose is the developer convenience, not a deployment path".)* Compose targets a developer laptop and a small/test single-host self-hoster (the shape ADR-0018's notebook spawner assumes); Helm targets multi-node self-hosting and managed multi-tenancy. The two are not kept in lockstep feature-for-feature, and the orchestration primitives of decisions 1–5 (rolling updates, autoscaling, the Ingress TLS edge, StatefulSets) are Kubernetes-only — a Compose deployment forgoes them by design. Where a value is authoritative it lives in one place per ADR-0000 (e.g. topic names in configuration consumed by both), not copied between them.

## Alternatives considered

**A. Keep docker-compose as the *only* deployment mechanism.** *Rejected:* it cannot provide rolling updates, autoscaling, self-healing, or multi-node scheduling, and has no managed-multi-tenant story — exactly what ADR-0001's managed offering commits to. Kubernetes/Helm is required for the managed offering and for scaled self-hosting. *(rev 1: this rejects Compose as the only mechanism, not Compose as a deployment shape — single-host Compose is a supported self-hosted target per decision 6 and ADR-0018. Rev 0 over-stated this as "Compose is not the deployment path.")*

**B. Package with raw manifests or Kustomize instead of Helm.** *Rejected:* raw manifests cannot parameterize per environment, and Kustomize overlays express environment differences but not cleanly the *optional in-cluster-vs-external dependency* toggle that decision 3 needs. Helm's conditionals and values fit the self-hosted/managed split directly.

**C. Always run stateful infrastructure in-cluster.** *Rejected:* a managed cloud deployment wants managed Postgres/Kafka/object-store for their durability and operational guarantees; forcing in-cluster StatefulSets there is a step backwards. The toggle (decision 3) keeps in-cluster as the self-host default without imposing it on managed.

**D. Always require external managed datastores.** *Rejected:* a self-hoster must be able to run the whole stack on their own cluster with no external dependencies. Again the toggle serves both.

**E. Use dedicated operators for the stateful services (e.g. a Postgres/Kafka operator).** *Deferred, not rejected:* operators run stateful services better than hand-written StatefulSets at scale and are the likely future direction, but they add heavy dependencies and are more than this first pass needs. StatefulSets now; operators revisited when operational load justifies them.

**F. Run the tracking server as a StatefulSet, as rev 1 said *(rev 2)*.** *Rejected:* a StatefulSet buys stable identity and per-pod storage, and the tracking server uses neither; it would only make the server harder to roll and scale while its real state stays where it already is.

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
- **Database initialization (decision 3).** The TimescaleDB StatefulSet now mounts the
  canonical `infrastructure/timescaledb/init-scripts` (databases, roles, schemas, tenant
  machinery) at `/docker-entrypoint-initdb.d` via a ConfigMap, so a fresh cluster is
  self-initializing — the same scripts the compose path runs (single source of truth: the
  chart reaches them through a `files/db-init` symlink that Helm follows). The five
  per-service role passwords are injected from the Secret. Without this the chart rendered but
  produced an empty database; this is what made it *cluster-functional*.
- **Durable Spark checkpoints (ADR-0006).** `spark-streaming` and `spark-aggregations` now get
  a ReadWriteOnce checkpoint PVC (`Recreate` strategy, single writer), so a restart resumes
  from committed Kafka offsets/state instead of reprocessing — matching the compose volumes.
- **Monitoring stack → ADR-0016.** The deferred Prometheus/Grafana/Loki decision is resolved
  there: the app chart emits ServiceMonitors / PrometheusRules / Grafana-dashboard ConfigMaps
  and ships the data-store exporters, while the backend (`kube-prometheus-stack` + Loki) runs
  separately (`deploy/observability/`). Tenant sensor alerts stay out of the shared stack.
- **Image registry and digest pinning.** Images are built, scanned (gating on fixable critical
  vulnerabilities), and published to the project's container registry by CI, with a moving tag
  and a per-commit tag. In production the chart references every image by **immutable digest**:
  third-party images are pinned by digest in the chart's values; first-party images carry an
  optional per-image digest that, when set, wins over the mutable tag. Because first-party digests
  rotate per build, the production overlay of digests is *generated*, not hand-maintained. This
  resolves the original "no pinned images" review concern. (Registry, tags, the generator and how
  to apply its output: `deploy/helm/industryflow/README.md`.)
- **Object-store image source (2026-10-11).** The upstream MinIO images were withdrawn from public
  registries. The in-cluster object store now runs a maintained, signed third-party build of the same
  server, pinned by digest like every other third-party image. It runs non-root, so the store's
  volume is owned by that user. The decision is unchanged; only the image's publisher moved.
- **Database role passwords (2026-10-17).** The count of five in the database-initialization entry
  held when it was written. The database has since gained a read-only monitoring role, the
  cold-layer exporter's role (ADR-0025) and the notebook SQL proxy's enrolment role (ADR-0015); the
  last two exist only when their component is deployed. The chart derives the set of role passwords
  from one list, which feeds both the database's initialization and the Secret it reads, so the
  set cannot diverge between the two and this record no longer carries a count.
- **Secret scoping (decision 4, 2026-10-17).** The chart renders one Secret per workload, holding
  only the keys that workload uses, rather than one Secret shared by every pod; an operator may
  instead supply a single existing Secret and scope it in the backend. The chart refuses to render
  while any secret value is still a placeholder. The choice of secrets backend remains deferred.
- **Autoscaling mechanism (2026-10-17).** The chart carries an opt-in autoscaler on CPU
  utilization for the request-serving stateless services, off by default, together with disruption
  budgets and replica spreading for the stateless tier. That is the mechanism only: which workloads
  scale, and on which metrics and thresholds, remains the deferred autoscaling policy.
- **Components packaged under their own ADRs (2026-10-17).** The chart also renders the notebook
  stack behind its own toggle (ADR-0011, ADR-0018), the device revocation-list freshness watch
  (ADR-0007 dec 8), the cold-layer export job (ADR-0025) and the database backup job (ADR-0032,
  proposed). Their decisions live in those records; the packaging decisions of this record apply
  to them unchanged.

## References

- ADR-0001 — framing; the self-hosted + commercial-managed split this chart's toggles serve.
- ADR-0002 — ingestion mTLS; the device-auth edge realized as the Ingress.
- ADR-0004 — API HTTPS; the browser-facing TLS termination realized as the Ingress.
- ADR-0006 — Spark windowing; durable checkpoints realized as PVCs.
- ADR-0000 — single source of truth; bounds the compose/Helm duplication.
