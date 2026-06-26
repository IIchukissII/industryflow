<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# IndustryFlow Helm chart

First-pass Kubernetes packaging for IndustryFlow, per **[ADR-0009](../../../ADR/ADR-0009-kubernetes-deployment-and-packaging.md)**.

**Scope (this pass):** the application services (api-gateway, ml-service API, alert-service API,
alert-service detector, ingestion-service, Spark streaming/aggregations) plus the core data
services (TimescaleDB, Kafka, Zookeeper, Redis, MinIO, MLflow). The monitoring stack, stateful
operators, autoscaling, and the mTLS device edge are deliberately later passes.

## Layout

```
deploy/helm/industryflow/
├── Chart.yaml
├── values.yaml                 # all knobs: images, replicas, resources, infra toggles, ingress, secrets
└── templates/
    ├── _helpers.tpl            # names, labels, infra host resolution (in-cluster vs external)
    ├── configmap.yaml          # non-secret config; resolves connection endpoints
    ├── secret.yaml             # PLACEHOLDER secret (do not ship real values)
    ├── apps.yaml               # Deployment (+ Service) per stateless app, ranged from values.apps
    ├── infra-timescaledb.yaml  # StatefulSet, gated by infra.timescaledb.inCluster
    ├── infra-kafka.yaml        # Zookeeper + Kafka StatefulSets
    ├── infra-redis.yaml
    ├── infra-minio.yaml
    ├── infra-mlflow.yaml       # Deployment (stateless front to DB + MinIO)
    ├── ingress.yaml            # the TLS edge (ADR-0002 / ADR-0004)
    └── NOTES.txt
```

## Self-hosted vs managed (ADR-0009 decision 3)

Each stateful dependency has an `inCluster` toggle in `values.yaml`:

- `inCluster: true` (default) renders the StatefulSet/Deployment + Service — the **self-hosted** model.
- `inCluster: false` renders nothing and the app config points at `infra.<dep>.external.*` — the
  **managed** model (external cloud Postgres/Kafka/object-store). The chart errors if an external
  endpoint is required but not supplied.

## Usage

```bash
# Render locally (no cluster needed)
helm template if deploy/helm/industryflow

# Lint
helm lint deploy/helm/industryflow

# Install (after providing real secrets + TLS cert)
helm install industryflow deploy/helm/industryflow \
  --namespace industryflow --create-namespace \
  --set image.tag=<digest-or-version> \
  --set-file ...   # or use a secrets backend
```

## Before production (also printed in NOTES.txt)

1. **Secrets** — replace the placeholder `secrets.data` with a real backend (SealedSecrets/Vault/CSI)
   or `secrets.create=false` + `secrets.existingSecret`. Never commit real values.
2. **Images** — pin `image.tag` by digest, not `latest`; publish images to `image.registry`.
3. **TLS** — provide the `ingress.tls.secretName` certificate (ADR-0004 public cert).
4. **DB init** — mount `infrastructure/timescaledb/init-scripts` via a ConfigMap once those scripts
   are fixed (the review found they abort on a fresh volume).
5. **Frontend** — has no Dockerfile yet; `apps.frontend.enabled=false` until it can be built.

## Known follow-ups (ADR-0009 deferred)

Monitoring stack chart · stateful operators (Postgres/Kafka) · HPA policies · secrets backend ·
image registry/CI · Spark-on-k8s · NetworkPolicies · the mTLS device Ingress.
