<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Uploading an externally-authored model — how the path works

[ADR-0030](../../ADR/ADR-0030-externally-authored-model-admission.md) decides *that* the platform
admits models it never watched being made, and *why* each refusal exists. This is its operational
companion: the shape of the flow, what each component is authoritative for, and what an operator
needs to know when something is refused. **The ADR is the why; nothing here overrides it.**

The requirement it serves: the cold layer ([ADR-0025](../../ADR/ADR-0025-cold-layer-historical-data-open-columnar.md))
makes years of history durable so it can be used, and training over that horizon is beyond what an
embedded notebook is meant to carry. The history goes out; the finished model comes back.

## The flow

```
  session ──► ml_service            mint an upload capability      (ADR-0030 dec 3)
                 │                  short-lived, one tenant, audience=upload
                 ▼
  client  ──► tracking gateway      stage: one pre-signed PUT per file
                 │                  keys are the gateway's, not the caller's
                 ▼
  client  ──► object store          bytes go DIRECT (ADR-0019 dec 6)
                 │                  into _upload-staging/<tenant>/<upload>/ — outside every
                 │                  tenant prefix, so nothing can address them yet
                 ▼
  client  ──► tracking gateway      commit: read each file's HEAD, judge on structure
                 │                  admitted → server-side copy into tenant_<uuid>/uploads/<id>/
                 │                  refused  → deleted, and the reason is returned
                 ▼
  client  ──► ml_service            register: declared semantics + compatibility  (dec 5/6)
                 │                  refused at 422 if this deployment cannot honour it
                 ▼
  client  ──► ml_service            deploy: load and score it here               (dec 7)
                                    refused at 409 if it has not been shown to work
```

## Who decides what, and why it is not all in one place

| Question | Answered by | Why there |
|---|---|---|
| May these bytes be written at all? | tracking gateway | It holds the **only** credential that may write the artifact store. Nothing else can refuse before the bytes exist. |
| What *are* these bytes? | tracking gateway | Structural, and needs no ML libraries: does loading this require deserialising author-supplied objects? |
| Can this deployment *serve* it? | ml_service | It **is** the serving environment, and the detector registry is discovered — a copy elsewhere would be wrong the day an operator installed an adapter. |
| Does its output *mean* anything here? | ml_service (registration) | The semantics vocabulary and the live registry live there. |
| Does it actually work here? | ml_service (deployment) | Only the real serving image can answer, and the verdict expires — so it is asked at the gate that already knows that. |

The gateway is the door for the **bytes**, not for the request. Admission policy stays with the side
that can judge it. See ADR-0030 dec 2 rev 1 if this looks like it could be simplified — it is the
shape a kernel-authored model already takes.

## What an uploader must provide

- **A model whose load path does not deserialise author-supplied objects.** In practice: save it in
  the format your framework offers that is not pickle. MLflow warns about this itself.
- **A declared input signature.** Without one the platform cannot construct an input to score, so it
  cannot establish that the artifact works here — and a check that cannot run is not a pass.
- **Declared output semantics**, from the vocabulary the registry advertises. There is no default:
  what a model's numbers *mean* is not recoverable from the numbers.
- **Complete declared requirements.** MLflow infers these, and its inference is not complete; nothing
  watched this artifact being trained, so what it carries is what someone wrote.

## Reading a refusal

| Where | Code | It means |
|---|---|---|
| commit | 422 | The artifact carries executable object-serialisation, declared or actual. Re-save it in a non-executing format. |
| commit | 404 | Nothing is staged under this upload for your tenant. An upload id from elsewhere buys nothing. |
| register | 422 | Either this environment cannot honour what the artifact declares, or nothing here can score that flavor with those semantics. The response says which. |
| deploy | 409 | It was registrable, but it did not load and score here. `stage` in the response says how far it got: `manifest`, `signature`, `load`, `score`, `timeout`, or `crash`. |
| any | 401 | The capability was absent, expired, revoked, or belongs to another plane. A kernel's tracking handle is not an uploader's. |

A refusal is the answer, not a failure of the platform to try harder: serving a model whose output
this deployment cannot interpret produces a confident wrong number, and the drift and alert lanes
believe those numbers.

## Operating it

- **The capability store must be reachable.** Without it `ml_service` mints nothing and says so
  (`501`) — a deployment with no store accepts no externally-authored models. This is the shared
  capability store, not the Redis feature store that was removed from `ml_service`.
- **Staging expires.** Admitted and refused uploads are cleaned at commit; the lifecycle rule exists
  for uploads that are staged and never committed, which nothing in the request path returns for.
  Compose sets it in `minio-init`; the chart sets it in a post-install hook. If you manage bucket
  lifecycle out-of-band, the expiry becomes **yours to provide** — without one, unadmitted bytes
  accumulate with nothing to collect them.
- **The staging prefix is known in three places** (the gateway, compose, the chart). They are checked
  against each other in the gateway's test suite, because drift there is silent: uploads keep
  working and the expiry quietly applies to a prefix nothing writes to.
- **Migrations do not re-run on an existing volume.** The provenance columns arrive with migration 19;
  on a live database apply it by hand, the way every init-script change has to be.

## Deliberately not built

- **Torch and any flavor outside the supported set.** Refused at the gate with a reason; the
  out-of-process serving boundary is [ADR-0028](../../ADR/ADR-0028-model-adapter-contract-and-score-semantics.md)
  dec 7's record to write. An upload path makes an unsupported flavor *more likely to arrive*, not
  more servable.
- **A drift baseline for uploaded models.** Without one they are visibly un-monitorable rather than
  silently so; where that baseline lives is deferred across ADR-0021/0028/0030 and should be closed
  by one record, not three.
- **Attestation.** The platform accepts the uploader's assertions and checks what it can observe. A
  verifiable claim about *who built this and from what* is the supply-chain question proper, and is
  separate from admission.
- **Resumable or very large transfers.** The transfer shape is deferred; today an upload is one
  request per file against a pre-signed URL.
