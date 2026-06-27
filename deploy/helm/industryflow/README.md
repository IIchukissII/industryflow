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
# Pin first-party images by digest: generate the overlay first, then apply it with -f.
scripts/pin-image-digests.sh <tag> > deploy/helm/industryflow/values-digests.yaml
helm install industryflow deploy/helm/industryflow \
  --namespace industryflow --create-namespace \
  -f deploy/helm/industryflow/values-digests.yaml \
  --set-file ...   # or use a secrets backend
```

### Image digest pinning

Production deployments run images by **immutable digest**, not mutable tags:

- **Third-party** images (TimescaleDB, Kafka/ZooKeeper, Redis, MinIO, the notebook
  proxy/nginx) are pinned as `name:tag@sha256:…` directly in `values.yaml`. Re-pin on upgrade.
- **First-party** `industryflow-*` images carry a per-image `digest:` (`apps.<name>.digest`,
  `infra.mlflow.digest`, `notebookHub.hub.digest`). Empty by default → the chart uses the
  mutable `image.tag` (dev convenience); when set, the digest wins over the tag. Because these
  digests rotate every build, generate the overlay rather than hand-editing:

  ```bash
  scripts/pin-image-digests.sh latest > deploy/helm/industryflow/values-digests.yaml
  ```

  The script resolves digests from GHCR via the registry API (`gh auth token` or `GITHUB_TOKEN`
  for private packages); no docker/skopeo needed.

## Before production (also printed in NOTES.txt)

1. **Secrets** — replace the placeholder `secrets.data` with a real backend (SealedSecrets/Vault/CSI)
   or `secrets.create=false` + `secrets.existingSecret`. Never commit real values. The chart
   **fails closed** if any value is still `CHANGE_ME` (`secrets.failOnPlaceholders`, default true;
   CI/dev renders set it false). Each value is the single source: the chart renders a **scoped
   Secret per workload** (only the keys that workload needs, e.g. `<release>-api-gateway-secrets`),
   so no pod receives the whole bundle, and each service connects as its own least-privilege DB
   role (`*_DB_USER` in config + `*_DB_PASSWORD` in its scoped Secret). With `existingSecret`, all
   workloads reference that one Secret — scope it at your backend.
2. **Images** — pin by digest (see *Image digest pinning* above): third-party in `values.yaml`,
   first-party via `scripts/pin-image-digests.sh > values-digests.yaml` + `-f`. Never ship `latest`.
3. **TLS** — provide two certs:
   - **Ingress** — the `ingress.tls.secretName` certificate (ADR-0004 public cert).
   - **Database (ADR-0017)** — the hardened `pg_hba` is `hostssl` + `scram-sha-256`, so an
     in-cluster TimescaleDB **requires** a server cert and every client connects `verify-full`.
     Either let the chart self-provision it with cert-manager — `--set tls.db.certManager.enabled=true`
     (a SelfSigned issuer → internal CA → server cert, mirroring `scripts/gen-internal-ca.sh`) —
     or supply a Secret (`tls.crt`/`tls.key`/`ca.crt`) from your own PKI as `tls.db.secretName`
     (default `industryflow-db-tls`). The DB pod mounts all three (an initContainer stages the key
     with `0600`/postgres-uid); clients mount only `ca.crt` and read `DB_SSLMODE`/`DB_SSLROOTCERT`
     from the shared ConfigMap. Without it the DB + clients stay pending. Knobs live under `tls.db`.
4. **DB init** — handled automatically when `infra.timescaledb.inCluster=true`: the canonical
   `infrastructure/timescaledb/init-scripts` are mounted at `/docker-entrypoint-initdb.d` via a
   ConfigMap and run on a fresh data volume. Set the five per-service role passwords in
   `secrets.data` (`*_DB_PASSWORD`) alongside `DB_PASSWORD`. For an **external** database
   (`inCluster=false`) run those scripts yourself before first use.
5. **Spark checkpoints** — by default `spark-streaming`/`spark-aggregations` run in local mode
   and claim a ReadWriteOnce PVC (`apps.<name>.persistence`); ensure a default StorageClass exists
   or set `storageClass`. **To scale Spark out** (separate executors / a real cluster), move the
   Structured-Streaming checkpoint to a shared store (ADR-0006): set
   `config.CHECKPOINT_LOCATION=s3a://spark-checkpoints`, add `MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY`
   to both spark workloads' `secretKeys`, and set their `persistence.enabled=false`. The images
   already carry the S3A jars; create the bucket in your object store first.

## Known follow-ups (ADR-0009 deferred)

Monitoring stack chart · stateful operators (Postgres/Kafka) · HPA policies · secrets backend ·
Spark-on-k8s.
