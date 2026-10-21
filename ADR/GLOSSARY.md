<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Glossary

One definition per term used in the [ADRs](.), `docs/` and code (ADR-0000 dec 3). A definition says
what a word means and points at the ADR that decides the behaviour; where the two disagree, the ADR
wins.

## Platform and multi-tenancy

| Term | Meaning | ADR |
|---|---|---|
| Open-core | One open codebase under the copyleft licence; the commercial offering is a licence exception for closed integration, not withheld features | ADR-0001 dec 1, 3 |
| Tenant | One customer organisation, identified by `company_id` (a UUID). "Tenant" in prose, `company_id` for the identifier | ADR-0001, ADR-0003 |
| Suspend | Reversibly stop a tenant: nothing of it is honoured or issued, and its data is kept | ADR-0033 dec 3 |
| Delete (tenant) | Irreversibly remove a suspended tenant from every store that holds its data | ADR-0033 dec 4, 7 |
| Tombstone | The registry entry a deleted tenant leaves, refused as deleted and never reused | ADR-0033 dec 8 |
| Schema-per-tenant | Each tenant's data lives in its own PostgreSQL schema `tenant_<company_id>` (hyphens as underscores), not in shared tables with a tenant column | ADR-0003 |
| Tenant-to-schema resolution | Mapping a verified caller's `company_id` to its schema through one validated, shared implementation | ADR-0003 dec 1-7 |
| Trusted mediation | Trusted server-side code holds the privileged principal and forces every operation into the caller's tenant; the caller presents only a narrow capability. Instances: API gateway, SQL proxy, tracking gateway, cold-store broker, upload path. A new tenant-access path reuses it | ADR-0003, ADR-0012, ADR-0015, ADR-0019, ADR-0025 dec 5, ADR-0030 dec 2-3 |
| Device / gateway | A field unit that produces readings and authenticates to the platform as an mTLS client; it generates its own hardware-bound key, which the CA never holds | ADR-0002, ADR-0007 dec 2 |
| Extension / plugin | A domain-specific addition (entities, types, transforms, detectors, adapters) loaded into the domain-generic core through the in-process registry by configured module, never by editing core code. "Extension" is the contract, "plugin" an instance | ADR-0008, ADR-0010 |

## Certificates

Three separate roots of trust; never describe them as one.

| Term | Meaning | ADR |
|---|---|---|
| Device CA | The client-authentication CA for ingestion mTLS: signs device requests, never holds a device key, signs no server certificate | ADR-0007 |
| Public edge certificate (managed) | The browser-facing server certificate of the managed deployment: an auto-renewed public ACME certificate | ADR-0004 dec 8 |
| Internal edge+DB CA (self-hosted) | The self-hosted CA whose root clients install once; it signs the edge certificate and the database certificate. A single host started without it self-signs the database certificate. Issuance: `docs/operations/tls.md` | ADR-0004 dec 8, ADR-0017 dec 3 |
| mTLS | Mutual TLS: the device presents a Device CA certificate and verifies the server. Device ingestion only | ADR-0002 |
| `verify-full` | PostgreSQL TLS mode that encrypts and verifies the server certificate, hostname included, against the deployment's database trust anchor; as opposed to encrypt-only `require` | ADR-0017 dec 1, 3 |

## Sessions and API access

| Term | Meaning | ADR |
|---|---|---|
| Platform session | A browser's authenticated session: it persists through refresh-credential rotation while each request carries a short-lived access token | ADR-0004 dec 2 |
| Access token | A JWT valid for minutes, carrying the signed tenant claim | ADR-0004 dec 2, ADR-0003 dec 2 |
| Refresh token | The server-side credential that renews the session: each use claims it atomically and issues a successor, so reuse is refused. Logout, compromise, a password change or reset, and deactivating or deleting the account or its company end all the user's sessions and the notebook capabilities minted from them | ADR-0004 dec 2 |
| httpOnly session cookie | How the browser holds the tokens: `httpOnly`, `Secure`, `SameSite` cookies set by the backend, unreadable by page JavaScript | ADR-0004 dec 3 |
| Notebook SSO | Signing a user into the notebook hub from the platform session ("who are you"); distinct from a capability ("what may this kernel touch") | ADR-0014 |

## Data pipeline

| Term | Meaning | ADR |
|---|---|---|
| At-least-once | The Kafka guarantee: a reading is never lost but may be delivered more than once; safe because every consumer's effect is idempotent | ADR-0005 dec 1, 6 |
| Idempotent write | A sink write with the same end state however often applied, keyed on the natural key: raw measurements first-write-wins, aggregates replace-on-conflict | ADR-0005 dec 6, ADR-0006 dec 3 |
| Dead-letter | Where a message no retry could process (malformed, unknown tenant or device, unwritable) goes, with its reason, to a topic owned by one consumer. Only infrastructure failures fail a batch. The aggregation stream excludes the same readings without a dead-letter topic of its own | ADR-0005 dec 3, ADR-0006 dec 4 |
| Watermark | The streaming bound on how late an event may arrive; it closes a window so its state is released | ADR-0006 dec 1 |
| Stateful transform | A feature transform that reads an external substrate (the windowed aggregates) rather than only the current reading; declared at registration; the class the kill-switch neutralizes | ADR-0024 dec 1, ADR-0023 |
| Cold layer | Raw measurements exported from the hot store to Parquet in the object store before hot retention drops them; for training and analysis, never live queries. Past the hot horizon it holds the only copy, so it is backed up | ADR-0025, ADR-0032 dec 4 |
| Archived part | One immutable unit of a day's raw history in the cold layer. Late rows become a further part; a part whose drop is recorded is never rewritten; readers read every part | ADR-0025 dec 3-4 |

## Notebooks

| Term | Meaning | ADR |
|---|---|---|
| Notebook hub | The multi-tenant JupyterHub service that spawns per-user notebook environments | ADR-0011 dec 1 |
| Spawner | The hub component that creates a user's environment: a per-user pod on Kubernetes (KubeSpawner), a per-user container on a Docker host (DockerSpawner), selected by configuration | ADR-0018 dec 1 |
| Capability handle | An opaque, high-entropy token presented instead of a store credential. Its authority is a short-lived, revocable entry in the shared store binding one user, one tenant, one audience, and whether the plane may write; a missing or incomplete entry denies | ADR-0015 dec 1-4, ADR-0012 |
| Audience | The plane a handle is bound to. A mediator honours only its own audience, which also decides the admission rules where two populations meet | ADR-0015 dec 3 |
| Plane | One path by which a tenant-facing caller reaches a shared store: a mediator, its audience, the store behind it. Named by what it reaches (data API, SQL, tracking, cold history, model upload); added by the record that introduces it | ADR-0015, ADR-0019, ADR-0025, ADR-0030 |
| Spawn-minting | The spawner minting a kernel's handles at spawn from the verified hub identity; the kernel cannot mint, refresh or widen them. A spawn-minted handle lives while its environment runs and is revoked when it stops, before a respawn, and at sign-out | ADR-0015 dec 2, 4; ADR-0012 dec 1 |
| Demand-minting | A designated minter minting a handle when a person asks, for a plane whose holder never spawns: tenant from a verified session only, handle spent by its one operation, no session secret distributed | ADR-0015 dec 4, ADR-0030 dec 3 |
| SQL proxy | The mediator for a notebook's SQL: it redeems the SQL handle and opens each upstream session as the tenant's own confined login identity, re-checks the handle while the session is open and closes it once revoked. The kernel never holds a database credential | ADR-0012 dec 4, ADR-0015 dec 2, 5-6 |
| Per-tenant read-only role | The standing per-tenant database principal with read-only grants on one tenant's schema, reached only through the SQL proxy's login identity for that tenant. Confinement is a property of who is logged in, not of a role switch (ADR-0015 dec 5). Names and grants: the database init scripts | ADR-0011 dec 3, 6; ADR-0012 dec 4; ADR-0015 dec 5 |

## Models

| Term | Meaning | ADR |
|---|---|---|
| Model artifact path | Authoring kernel → tracking gateway → registry → `ml_service`. What crosses is a serialised model, not a pickle: sklearn as skops, xgboost as a native booster | ADR-0027, ADR-0019, ADR-0010 dec 6 |
| Train/serve parity | The artifact declares its requirements; the serving environment satisfies them or refuses the model. Per-library version constraints instantiate the rule and are a floor on divergence, not a pin | ADR-0027 dec 1, 3 |
| Supported flavor set | What serving can honour: in-process flavors plus installed model adapters; closed and declared, while the authoring set is open. A model outside it is refused with a reason | ADR-0027 dec 2 |
| Model adapter | A detector registered with the score semantics it implements and the artifact flavors it scores. In-process by default; a framework the image does not carry is refused at the gate | ADR-0028, ADR-0008 dec 3 |
| Score semantics | What a model's output means: `anomaly_probability`, `outlier_score`, `reconstruction_error` or `direct_score`. Declared by the detector recorded at registration, never inferred from the output; a model whose semantics cannot be established is refused | ADR-0028 dec 1-2 |
| Provenance | Where a model was authored: in the platform or outside it and uploaded. Derived from how the model arrived, never taken from the caller or synthesised; serving grants deserialisation trust by provenance | ADR-0030 dec 8, ADR-0027 dec 13 |
| Compatibility status | Whether serving still satisfies what a registered model declares; surfaced where models are looked at, never routed into the retrain recommendation | ADR-0027 dec 7-8 |

## Deployment

Self-hosted and managed are a business distinction (ADR-0001 dec 4); the deployment shapes are ADR-0009.

| Term | Meaning | ADR |
|---|---|---|
| Self-hosted | A customer running the platform on its own infrastructure, as single-host Compose or in-cluster Helm | ADR-0001 dec 4, ADR-0009 dec 6 |
| Managed (commercial) | IndustryFlow operating the platform as a service: Kubernetes and Helm, usually with external datastores | ADR-0001, ADR-0009 dec 1, 3 |
| Compose deployment | `docker-compose.yml`: the development path and a supported single-host deployment, not in feature lockstep with Helm | ADR-0009 dec 6 |
| Box run / box-validated | A human run of a merged commit on the long-lived Compose deployment; post-merge discovery, not a merge gate | ADR-0026 dec 2, 6 |
| `inCluster` toggle | The per-dependency Helm switch: stateful infrastructure in-cluster (self-hosted) or an external endpoint (managed) | ADR-0009 dec 3 |

## ADR vocabulary

| Term | Meaning | ADR |
|---|---|---|
| ADR | Architecture Decision Record: the decision in force and its rationale; artifacts own the values. Files `ADR/ADR-NNNN-kebab-title.md` | ADR-0000 dec 2, 5, 8 |
| Status | Header field: `Proposed`, `Accepted` or `Superseded by ADR-NNNN`; lifecycle only | ADR-0000 dec 7, 9 |
| Date | Header field: the acceptance date, plus the date of the latest change | ADR-0000 dec 5, 9 |
| Parent | Header field: the broader ADR this one is decided under; optional. No ADR names ADR-0000 | ADR-0000 dec 6, 9 |
| Related | Header field: other ADRs of this repository and what each relation is | ADR-0000 dec 9 |
| Decision number | The stable number of a decision within an ADR; cited as `ADR-NNNN dec N`, never renumbered or reused | ADR-0000 dec 8 |
| Single source of truth | Every fact has one authoritative home and is referenced elsewhere | ADR-0000 dec 3 |
