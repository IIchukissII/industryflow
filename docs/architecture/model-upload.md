<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Model upload

The path that admits an externally-authored model: components, locations, settings and refusals.

| Decision | ADR |
|---|---|
| Admission, structural refusal, registration and deployment gates, browser reachability | [ADR-0030](../../ADR/ADR-0030-externally-authored-model-admission.md) |
| Bytes go direct to the store; the gateway is the only writer | [ADR-0019](../../ADR/ADR-0019-notebook-experiment-tracking-gateway.md) dec 6 |
| Supported flavor set, skops trust by provenance | [ADR-0027](../../ADR/ADR-0027-model-artifact-supply-chain-parity.md) |
| Declared score semantics, detector per model | [ADR-0028](../../ADR/ADR-0028-model-adapter-contract-and-score-semantics.md) |
| Single origin | [ADR-0004](../../ADR/ADR-0004-api-authentication-sessions-and-transport.md) |

## Flow

```
session ─▶ ml-service-api              POST /api/models/upload-capability   (audience upload)
client  ─▶ notebook-tracking-gateway   stage: one pre-signed PUT URL per file
client  ─▶ object store                PUT into _upload-staging/<tenant>/<upload id>/
client  ─▶ notebook-tracking-gateway   commit: judge each file's head; admit → copy to tenant_<uuid>/uploads/<upload id>/, refuse → delete
client  ─▶ ml-service-api              POST /api/models (admitted artifact_uri, declared semantics, feature config)
client  ─▶ ml-service-api              POST /api/models/{model_id}/deploy (load-and-score probe)
alert worker ─▶ ml-service-api         POST /api/inference/predict (loaded from the admitted location)
```

## Components

| Name | Role | Port | Config |
|---|---|---|---|
| `notebook-tracking-gateway` | Stage and commit; the only writer to the artifact store; judges at commit whether loading deserialises author-supplied objects | 5050 | `services/notebook_tracking_gateway/gateway.py`, `staging.py`, `admission.py` |
| `ml-service-api` | Mints the upload capability handle; at registration, checks the flavor and semantics are servable; at deployment, runs the load-and-score probe | 8002 | `services/ml_service/api/upload_capability.py`, `routers/models.py`, `model_compat.py`, `uploaded_model.py`, `load_probe.py`, `load_probe_worker.py` |
| Upload wizard | Models page, **Upload model** | | `services/frontend/src/pages/ModelUploadWizard.jsx` |

## Locations

| Item | Value |
|---|---|
| Staging prefix | `_upload-staging/` (`UPLOAD_STAGING_PREFIX`; gateway, `minio-init`, Helm `job-minio-init.yaml`) |
| Admitted location | `s3://<bucket>/tenant_<uuid>/uploads/<upload id>` |
| Bucket | `UPLOAD_ARTIFACT_BUCKET` (`ml-service-api`) = `TRACKING_ARTIFACT_BUCKET` (tracking gateway) |
| Edge routes | [API reference](../api/README.md#edge-routes) |

Registration accepts only the admitted location under the caller's tenant; a model naming both a run
and an artifact is refused. The check runs at registration, deployment and every load (warm-cache
hits included).

The author-code check (`skops_trust.refuse_author_code`) runs for every model, uploaded or kernel-authored
([ADR-0037](../../ADR/ADR-0037-no-author-code-on-the-serving-side.md)), at registration (422), deployment
(409) and every load. It reads the manifest only and imports nothing it names:

| Refused | Detail |
|---|---|
| `code` in any flavor | Directory on the import path |
| Loader other than `mlflow.sklearn` or `mlflow.xgboost` | Deny by default (`PLATFORM_LOADERS`), checked by name |
| `mlflow.sklearn` not in skops format | Object deserialisation |
| xgboost `model_class` outside xgboost's model classes | Class the artifact names |
| A `data` path outside the model directory | Checked at load |

MLflow's pickle switch (`MLFLOW_ALLOW_PICKLE_DESERIALIZATION`) is off in the serving process and the
probe process for their whole life, set by the process itself; a deployment setting cannot turn it on.

The compatibility gate decides a flavor by name: in the gate's flavor map or claimed by a loaded
detector, never by importing the module the manifest names.

The deployment probe loads with uploaded-provenance trust (no skops grant) and runs with only
interpreter settings and the object-store credential.

## Settings

| Name | Default | Meaning |
|---|---|---|
| `PUBLIC_BASE_URL` (Compose; `ml-service-api`, tracking gateway) | empty | Public edge origin. Set: pre-signed PUTs are signed for the edge (`TRACKING_S3_PRESIGN_ENDPOINT_URL`) and advertised as the upload endpoint (`UPLOAD_GATEWAY_URL`). Empty: interior endpoint only; the wizard reports browser upload as not configured |
| `UPLOAD_ARTIFACT_BUCKET` (`ml-service-api`) | `mlflow` (Compose) | Unset: no uploaded model can be registered or served |
| `UPLOAD_STAGING_EXPIRE_DAYS` (`minio-init`); Helm `notebookHub.trackingGateway.uploadStaging.expireDays` | 1 | Lifecycle expiry for staged, never-committed uploads. With bucket lifecycle managed elsewhere, the expiry must be set there |

Pre-signed URL lifetime: [tracking gateway settings](../../services/notebook_tracking_gateway/README.md#settings).

## Uploader requirements

| Requirement | Refused without it at |
|---|---|
| A load path that does not deserialise author-supplied objects (not pickle) | commit (422) |
| A loader the platform supplies (`mlflow.sklearn` skops, `mlflow.xgboost`), no shipped code | register (422) or deploy (409) |
| A declared input signature | deploy (409, stage `signature`) |
| Declared output semantics from the vocabulary in `GET /api/ml/capabilities` | register (422) |
| Complete declared requirements | register (422) or deploy (409) |
| A feature config (wizard field) | Stored, but not scorable; register a new version with one |

## Refusals

| Where | Code | Meaning |
|---|---|---|
| commit | 400 | Unknown upload id |
| commit | 401 | Upload capability absent, expired or already used |
| commit | 404 | Nothing staged under this upload for the caller's tenant |
| commit | 422 | Executable object serialisation, declared or actual; or a manifest over the size limit |
| commit | 409 | A staged file changed while it was judged |
| register | 422 | Loading would run author code; the environment cannot honour the declared requirements; or no detector scores that flavor with those semantics |
| register | 503 | Manifest unreadable (transient) |
| deploy | 409 | Loading would run author code |
| deploy | 409 | Did not load and score; `stage` is `manifest`, `signature`, `trust`, `load`, `score`, `timeout`, `crash` or `invoke` |
| upload-capability | 501 | No capability store configured; no external model is accepted |
| any | 401 | Capability absent, expired, revoked or for another plane |

## Operating notes

| Item | Detail |
|---|---|
| Staging-prefix agreement | Gateway, Compose and chart values checked against each other by `services/notebook_tracking_gateway/tests/test_staging_infra_parity.py` |
| Script 20 on a live database | Backfills `detector = sklearn` for kernel-trained rows only; an upload registered before it must be registered again |

## Not supported

| Item | Status |
|---|---|
| Torch and other flavors outside the supported set | Refused with a reason ([ADR-0028](../../ADR/ADR-0028-model-adapter-contract-and-score-semantics.md) dec 7) |
| Uploaded tree-based sklearn models | Refused at deploy, stage `trust` ([ADR-0027](../../ADR/ADR-0027-model-artifact-supply-chain-parity.md) dec 13) |
| Drift baseline for uploaded models | Not built ([ADR-0030](../../ADR/ADR-0030-externally-authored-model-admission.md)) |
| Attestation of origin | Not built |
| Resumable or multipart transfer | Not built ([ADR-0030](../../ADR/ADR-0030-externally-authored-model-admission.md) dec 10) |
