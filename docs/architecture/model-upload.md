<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Model upload

The manual of [ADR-0030](../../ADR/ADR-0030-admission-of-externally-authored-models.md): the path that admits an externally-authored model, from upload to serving.

## ADR-0030 dec 1

The Models page's **Upload model** wizard takes a model trained off the platform through upload,
registration and deployment.

| Code | Role |
|---|---|
| `services/frontend/src/pages/ModelUploadWizard.jsx` | The upload wizard |
| `services/ml_service/tests/test_upload_serving_integration.py::test_an_admitted_upload_registers_loads_scores_and_deploys` | Proof: an admitted upload registers, loads, scores and deploys |

### Flow

```
session ─▶ ml-service-api              POST /api/models/upload-capability   (audience upload)
client  ─▶ notebook-tracking-gateway   stage: one pre-signed PUT URL per file
client  ─▶ object store                PUT into _upload-staging/<tenant>/<upload id>/
client  ─▶ notebook-tracking-gateway   commit: judge each file's head; admit → copy to tenant_<uuid>/uploads/<upload id>/, refuse → delete
client  ─▶ ml-service-api              POST /api/models (admitted artifact_uri, declared semantics, feature config, equipment, feature sample)
client  ─▶ ml-service-api              POST /api/models/{model_id}/deploy (load-and-score probe)
alert worker ─▶ ml-service-api         POST /api/inference/predict (loaded from the admitted location)
```

### Components

| Name | Role | Port | Config |
|---|---|---|---|
| `notebook-tracking-gateway` | Stage and commit; the only writer to the artifact store; judges at commit whether loading deserialises author-supplied objects (ADR-0030 dec 2) | 5050 | `services/notebook_tracking_gateway/gateway.py`, `staging.py`, `admission.py` |
| `ml-service-api` | Mints the upload capability handle; at registration, checks the flavor and semantics are servable; at deployment, runs the load-and-score probe (ADR-0030 dec 3, 5, 7) | 8002 | `services/ml_service/api/upload_capability.py`, `routers/models.py`, `model_compat.py`, `uploaded_model.py`, `load_probe.py`, `load_probe_worker.py` |
| Upload wizard | Models page, **Upload model** (ADR-0030 dec 1) | | `services/frontend/src/pages/ModelUploadWizard.jsx` |

### Locations

| Item | Value |
|---|---|
| Staging prefix | `_upload-staging/` (`UPLOAD_STAGING_PREFIX`; gateway, `minio-init`, Helm `job-minio-init.yaml`) |
| Admitted location | `s3://<bucket>/tenant_<uuid>/uploads/<upload id>` |
| Bucket | `UPLOAD_ARTIFACT_BUCKET` (`ml-service-api`) = `TRACKING_ARTIFACT_BUCKET` (tracking gateway) |
| Edge routes | [API reference](../api/README.md#edge-routes) |

### Settings

| Name | Default | Meaning |
|---|---|---|
| `PUBLIC_BASE_URL` (Compose; `ml-service-api`, tracking gateway) | empty | Public edge origin. Set: pre-signed PUTs are signed for the edge (`TRACKING_S3_PRESIGN_ENDPOINT_URL`) and advertised as the upload endpoint (`UPLOAD_GATEWAY_URL`). Empty: interior endpoint only; the wizard reports browser upload as not configured |
| `UPLOAD_ARTIFACT_BUCKET` (`ml-service-api`) | `mlflow` (Compose) | Unset: no uploaded model can be registered or served |
| `UPLOAD_PROBE_TIMEOUT_SECONDS` (`ml-service-api`) | `120` | Load-and-score probe at deploy; longer is a refusal, stage `timeout` |
| `UPLOAD_PROBE_SAMPLE_TIMEOUT_SECONDS` (`ml-service-api`) | `300` | Scoring the feature sample after the probe's verdict; longer leaves the model without prediction drift, not refused |
| `UPLOAD_STAGING_EXPIRE_DAYS` (`minio-init`); Helm `notebookHub.trackingGateway.uploadStaging.expireDays` | 1 | Lifecycle expiry for staged, never-committed uploads. With bucket lifecycle managed elsewhere, the expiry must be set there |

Pre-signed URL lifetime: [tracking gateway settings](../../services/notebook_tracking_gateway/README.md#settings).

### Refusals

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

## ADR-0030 dec 2

The gateway refuses an artifact with executable content; only the gateway's principal writes the
staging area. The gateway is the artifact store's only writer
([ADR-0019](../../ADR/ADR-0019-notebook-experiment-tracking-gateway-and-the-tracking-capability.md) dec 6).

| Code | Role |
|---|---|
| `services/notebook_tracking_gateway/admission.py` | Structural refusal |
| `infrastructure/minio/minio-init.sh` | Object-store principals and policies |

## ADR-0030 dec 3

| Step | Mechanism |
|---|---|
| Mint | `POST /api/models/upload-capability` with a verified session and a writing role; `ml-service-api` writes an opaque handle into the shared capability store |
| Bound facts | User, tenant from the session, audience `upload`, `read_only: false`, `handle_id`; expiry 600 s |
| Refusals at mint | No user or tenant (400), tenant not a UUID (400), tenant suspended (403), no capability store (501) |
| Index and record | The handle is indexed under its user and its mint recorded, in one transaction |
| Resolve | The gateway's upload surface resolves the bearer only as an `upload`-audience entry; any other audience, or none, is 401 |
| Tracking plane | An `upload` handle does not resolve on the kernels' tracking plane |

The gateway holds no session-signing key.

| Code | Role |
|---|---|
| `services/ml_service/api/upload_capability.py::build_record` | The bound facts; refuses without a user or tenant |
| `services/ml_service/api/upload_capability.py::AUDIENCE_UPLOAD` | The upload audience |
| `services/ml_service/api/routers/models.py::mint_upload_capability` | The mint route |
| `services/ml_service/api/main.py` | Connects the capability store |
| `services/notebook_tracking_gateway/policy.py::resolve_upload_outcome` | Resolves an upload-audience handle |
| `services/notebook_tracking_gateway/gateway.py` | `uploader`: 401 unless the bearer is an upload handle |
| `services/frontend/src/pages/ModelUploadWizard.jsx` | Mints first and keeps the handle in memory only |
| `services/ml_service/tests/test_upload_capability.py` | Proof: mint binding, expiry, refusals, one-transaction record |
| `services/notebook_tracking_gateway/tests/test_upload_audience.py` | Proof: upload and tracking audiences are not interchangeable |

## ADR-0030 dec 4

The gateway refuses executable serialisation at commit. The author-code check
(`skops_trust.refuse_author_code`) runs for every model, uploaded or kernel-authored
([ADR-0037](../../ADR/ADR-0037-no-model-load-on-the-serving-side-runs-author-code-whatever-the-provenance.md)), at registration (422), deployment
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

The deployment probe loads with uploaded-provenance trust (no skops grant) and runs with only
interpreter settings and the object-store credential.

| Code | Role |
|---|---|
| `services/notebook_tracking_gateway/admission.py` | Refuses executable serialisation at commit |
| `services/ml_service/api/skops_trust.py::refuse_author_code` | The author-code check |
| `services/ml_service/api/skops_trust.py::PLATFORM_LOADERS` | The platform loaders |
| `services/ml_service/api/skops_trust.py::executable_deserialisation_off` | Holds MLflow's pickle switch off in the process |
| `services/ml_service/tests/test_upload_author_code.py` | Proof: shipped code and an author-named loader are refused without importing them |

### Not supported

| Item | Status |
|---|---|
| Uploaded tree-based sklearn models | Refused at deploy, stage `trust` ([ADR-0027](../../ADR/ADR-0027-the-model-artifact-declares-the-serving-environment-satisfies-or-refuses.md) dec 13) |

## ADR-0030 dec 5-8

Registration judges the upload's declared semantics
([ADR-0028](../../ADR/ADR-0028-declared-score-semantics-and-the-model-adapter-contract.md)) and
requirements, grants trust by provenance and loads it in an isolated probe.

Registration accepts only the admitted location under the caller's tenant; a model naming both a run
and an artifact is refused. The check runs at registration, deployment and every load (warm-cache
hits included).

| Code | Role |
|---|---|
| `services/ml_service/api/uploaded_model.py` | Upload registration |
| `services/ml_service/api/skops_trust.py` | Trusted types by provenance |
| `services/ml_service/api/load_probe.py` | Isolated load-and-score |

### Uploader requirements

| Requirement | Refused without it at |
|---|---|
| A load path that does not deserialise author-supplied objects (not pickle) | commit (422) |
| A loader the platform supplies (`mlflow.sklearn` skops, `mlflow.xgboost`), no shipped code | register (422) or deploy (409) |
| A declared input signature | deploy (409, stage `signature`) |
| Declared output semantics from the vocabulary in `GET /api/ml/capabilities` | register (422) |
| Complete declared requirements | register (422) or deploy (409) |
| A feature config (wizard field) | Stored, but not scorable; register a new version with one |
| A feature sample and equipment (wizard fields, with a feature config) | Registered without a drift baseline; `baseline.reason` says why ([drift](ml-and-features.md#drift-post-apidriftevaluate)) |

### Operating notes

| Item | Detail |
|---|---|
| Script 20 on a live database | Backfills `detector = sklearn` for kernel-trained rows only; an upload registered before it must be registered again |

### Not supported

| Item | Status |
|---|---|
| Attestation of origin | Not built |

## ADR-0030 dec 9

The supported flavor set is the loaders the compatibility gate knows (`FLAVOR_LIBRARY`) and the
flavors the loaded detectors claim
([ADR-0027](../../ADR/ADR-0027-the-model-artifact-declares-the-serving-environment-satisfies-or-refuses.md) dec 2).
The compatibility gate decides a flavor by name: in the gate's flavor map or claimed by a loaded
detector, never by importing the module the manifest names.

| Code | Role |
|---|---|
| `services/ml_service/api/model_compat.py::FLAVOR_LIBRARY` | The flavor map |
| `services/ml_service/api/model_compat.py::_supported_flavor` | Decides a flavor by name |
| `services/ml_service/tests/test_uploaded_model.py::test_a_known_semantics_for_an_unservable_flavor_is_refused` | Proof: an upload of an unservable flavor is refused |
| `services/ml_service/tests/test_upload_author_code.py::test_a_flavor_a_loaded_detector_claims_is_in_the_supported_set` | Proof: a flavor a loaded detector claims is supported |

### Not supported

| Item | Status |
|---|---|
| Torch and other flavors outside the supported set | Refused with a reason ([ADR-0028](../../ADR/ADR-0028-declared-score-semantics-and-the-model-adapter-contract.md) dec 7) |

## ADR-0030 dec 10

The edge serves the upload plane and the staging area by sub-path on the app's origin (single origin,
[ADR-0004](../../ADR/ADR-0004-api-authentication-session-model-and-transport-security.md)); the gateway
stages the upload outside every tenant namespace and the client writes the bytes to the store under a
pre-signed PUT.

| Code | Role |
|---|---|
| `services/frontend/nginx.conf.template` | Upload routes at the edge |
| `services/notebook_tracking_gateway/staging.py` | Staging area |
| `services/notebook_tracking_gateway/tests/test_staging_infra_parity.py` | Proof: gateway, Compose and chart values agree on the staging prefix (ADR-0000 dec 3) |

### Not supported

| Item | Status |
|---|---|
| Resumable or multipart transfer | Not built |

## ADR-0030 dec 11

Deploying an upload is the deploy route every model takes, behind the writer-role gate; no
provenance-specific approval exists. For an upload, the route adds the load-and-score probe (dec 7)
to the gates every model passes.

| Code | Role |
|---|---|
| `services/ml_service/api/routers/models.py::deploy_model` | One deploy route for every provenance |
| `services/ml_service/api/auth.py::require_writer` | The writer-role gate |
