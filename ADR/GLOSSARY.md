<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Glossary

Authoritative definitions for the terms used across IndustryFlow's [ADRs](.), `docs/`,
and code. Its purpose is the one ADR-0000 sets out: a term that means slightly different
things in different documents is a drift source, so each term gets **one** definition here and
every other document uses it as written.

This file defines *what a word means*; it does **not** restate the decisions behind a subsystem
— those live in the ADR named against each term (the *why/what* split of ADR-0000). When a
definition and an ADR disagree, the ADR wins and this file is the bug.

---

## Platform and multi-tenancy

**Open-core** — as this project uses it: the whole platform is one open codebase under the
copyleft licence, and the commercial offering is a licence exception for closed integration, not a
set of withheld features. It does **not** carry the common feature-gated sense (an open core
wrapped in a closed shell of paid add-ons), which the framing ADR rejects. *(ADR-0001 dec 1, 3)*

**Tenant** — one customer organisation on the platform. Identified by `company_id`, a UUID.
"Tenant", "company", and `company_id` refer to the same thing; prefer "tenant" in prose and
`company_id` for the concrete identifier. *(ADR-0001, ADR-0003)*

**Schema-per-tenant** — the isolation model: each tenant's data lives in its own PostgreSQL
schema named `tenant_<company_id>` (UUID hyphens replaced by underscores), not in shared tables
with a tenant column. *(ADR-0003)*

**Tenant-to-schema resolution** — mapping a verified caller's `company_id` to its
`tenant_<…>` schema through one validated, parameterised implementation — *not* the
scan-all-schemas, string-spliced lookup the resolution ADR replaced. *(ADR-0003)*

**Trusted mediation** — the platform's single pattern for giving untrusted or tenant-facing
code scoped access to a shared store: trusted server-side code holds the privileged principal
and forces every operation into the caller's tenant, so the caller presents only a narrow
capability and never the store credential. Its instances are the API gateway scoping a query
(ADR-0003, ADR-0004), the SQL proxy scoping a database connection (ADR-0012, ADR-0015), the
experiment-tracking gateway scoping tracking, registry, and artifact calls (ADR-0013, ADR-0019),
the cold-store broker scoping reads of long-horizon history (ADR-0025 dec 5), and the model-upload
path, where the serving side authorises an upload and the tracking gateway remains the one writer
of the artifact store (ADR-0030 dec 2-3). A new tenant-access path reuses this pattern rather than
inventing a second exposure model. *(ADR-0003, ADR-0004, ADR-0012, ADR-0013)*

**Device / gateway** — a field unit that produces sensor readings and authenticates *to* the
platform as a client. On the reference deployment its private key is held in an ATECC secure
element and never leaves the hardware. *(ADR-0002, ADR-0007)*

**Extension / plugin** — a domain-specific addition (entities, types, feature transforms)
loaded into the otherwise domain-generic core via the in-process registry, configured by module
import and never by editing core code. "Extension" is the contract; "plugin" is an instance of
it. *(ADR-0008 defines the interface; ADR-0010 the in-process registry mechanism)*

---

## Identity, certificates, and the three CAs

The platform runs **three distinct certificate authorities / roots of trust**. They are easy to
conflate because all involve TLS; they are *not* the same root and must not be described as one.

**Device CA** — the internal **client-authentication** CA that signs device certificate-signing
requests for ingestion mTLS. It signs CSRs and **never holds a device's private key** (it has
its own signing key, as every CA does). It authenticates devices *to* the platform. It is **not**
used for any server certificate. *(ADR-0007 dec 2)*

**Public edge certificate (managed)** — the browser-facing API/frontend server certificate in
the **managed** deployment: a **publicly-trusted ACME / Let's Encrypt** certificate. It
authenticates the platform *to* browsers. A distinct PKI concern from the device CA.
*(ADR-0004 dec 8)*

**Internal edge+DB CA (self-hosted)** — the **internal** CA used in the **self-hosted / Compose**
deployment, where a public ACME cert is not obtainable for an internal hostname (e.g.
`industryflow.local`). It is the server-certificate root for both the frontend TLS edge and
TimescaleDB. A separate root from the Device CA. For the decision (why one internal root serves
both edge and database) see ADR-0017; issuance is described in `docs/operations/tls.md`. *(ADR-0004 dec 8 keys the edge
certificate to the deployment shape; ADR-0017 reuses this internal root for the database)*

**mTLS** — mutual TLS: the device presents a client certificate (Device CA) *and* verifies the
server certificate. Used only on the device-ingestion edge. *(ADR-0002)*

**`verify-full`** — the PostgreSQL TLS mode where the client both encrypts the connection and
verifies the DB server certificate (against the internal edge+DB CA) with hostname checking —
as opposed to encrypt-only (`require`). *(ADR-0017)*

---

## Sessions and API access

**Platform session** — a browser's authenticated session with the API. It **persists across
hours via refresh-token rotation**, but the credential carried on each request — the **access
token** — is **short-lived**. When prose calls the session "long-lived" it means *the session
persists*; when it calls it "short-lived" it means *the access token expires in minutes*. Both
are true of different parts of the same session; use the precise term below to avoid the
apparent contradiction. *(ADR-0004)*

**Access token** — a JWT with a lifetime on the order of **minutes**, carrying the tenant
claim relied on by tenant-to-schema resolution. Short-lived by design. *(ADR-0004 dec 2)*

**Refresh token** — a longer-lived credential tracked server-side and **rotated on each use**;
logout or compromise revokes it immediately, ending the session. This is what makes the *session*
outlast any single access token. *(ADR-0004 dec 2)*

**httpOnly session cookie** — how the browser holds the tokens: `httpOnly`, `Secure`,
`SameSite` cookies set by the backend, not `localStorage`, so page JavaScript cannot read them.
*(ADR-0004 dec 3)*

**Notebook SSO** — signing a user into the notebook hub from their existing platform session
("who are you"), as distinct from a capability ("what may this kernel touch"). *(ADR-0014)*

---

## Data pipeline

**At-least-once** — the Kafka delivery guarantee: a reading is never lost, but may be delivered
more than once. Safe only because the sinks are idempotent. *(ADR-0005)*

**Idempotent write** — a sink write that produces the same end state whether applied once or
many times, so at-least-once duplicates are harmless. It is keyed on the record's natural key, and
what a conflict means depends on what is written: a raw measurement is **first-write-wins** (a later
write of the same key is ignored), an aggregate is **replace-on-conflict** (the latest computation of
a window is kept). *(ADR-0005, ADR-0006 dec 3 rev 2)*

**Dead-letter** — the destination a message goes to when no retry could ever process it — malformed,
naming an unknown tenant or device, or otherwise unwritable — kept with its reason for inspection
instead of failing the batch forever. Each consumer has its own; only infrastructure failures fail a
batch, because only they are fixed by retrying. *(ADR-0005 dec 3; ADR-0006 dec 4 rev 2)*

**Stateful transform** — a feature transform that reads an external substrate (today, the
windowed aggregates) rather than being a pure function of the current reading. The class is
declared by the transform at registration, so the engine never hardcodes it, and it is the class
the kill-switch neutralizes. *(ADR-0024 dec 1; ADR-0023)*

**Cold layer** — the third storage layer: raw measurements exported from the hot store into an
open columnar format in the object store, before hot retention drops them, for training and
long-horizon analysis — never a live-query path. Past the hot horizon it holds the only copy of raw
history, which is why it is itself backed up. *(ADR-0025; ADR-0032 dec 4)*

**Archived part** — one immutable unit of a day's raw history in the cold layer. A day may be
archived in several parts, because rows can reach the hot store for a day after it was exported; each
part has its own fingerprint and its own recorded drop, and once its drop is recorded it is never
overwritten — later rows become a further part. Readers read every part of a day.
*(ADR-0025 dec 3-4 rev 2)*

**Watermark** — the Spark streaming bound on how late an event may arrive, which lets a time
window be declared closed so its aggregation state can be released — without it, streaming state
grows without bound. *(ADR-0006)*

---

## Notebooks

**Notebook hub** — the multi-tenant JupyterHub-style service that spawns a per-user notebook
environment. *(ADR-0011)*

**Spawner** — the hub component that creates a user's environment. It is realised as
**KubeSpawner** (a per-user **pod**) on Kubernetes and **DockerSpawner** (a per-user
**container**) on Compose; "per-user pod spawner" in ADR-0011 means *pod-or-container* under
this portability. *(ADR-0011 dec 1, refined by ADR-0018)*

**Capability handle** — an opaque, high-entropy token presented in place of a store credential.
It carries no authority itself: its authority is a short-lived, centrally revocable entry in the
shared capability store, bound to one user, one tenant and one **audience**, with read-only
recorded as a fact of the binding rather than assumed. A handle held by a kernel is
**spawn-minted** and lives for the session; a handle a person obtains for one operation is
**demand-minted** and is spent by that operation. A spawn-minted handle is kept alive only while its
environment runs, and is revoked when the environment stops or respawns and when the user's session
ends; an entry that is missing or incomplete denies. Every handle is redeemed only by the mediator of
its own audience. *(ADR-0012, ADR-0015 dec 1-4, rev 3)*

**Audience** — the plane a capability handle is bound to, recorded in its store entry. A mediator
honours only handles of its own audience, so one plane's handle never opens another's, and on a
mediator reached by more than one population the audience is what decides which admission rules a
request faces. Each audience is introduced by the record that introduces its plane.
*(ADR-0015 dec 3; ADR-0019, ADR-0025, ADR-0030)*

**Plane** — one path by which a tenant-facing caller reaches a shared store: a trusted mediator
(see *Trusted mediation*), the audience of the handles it honours, and the store behind it. Planes
are named by what they reach (data API, SQL, tracking, cold history, model upload), never counted:
a plane is added by the record that introduces it. *(ADR-0015, ADR-0019, ADR-0025, ADR-0030)*

**Spawn-minting** — the spawner minting a kernel's handles at spawn, from the verified identity of
hub entry, and injecting them into the environment. The only way a kernel receives anything.
*(ADR-0015 dec 4, ADR-0012 dec 1)*

**Demand-minting** — a trusted component minting a handle at the moment a person asks for one, for
a plane whose holder never spawns. Permitted only for the minter the plane's record designates,
with the tenant taken from a verified platform session, the handle spent by the one operation it
authorises, and no session secret distributed to enforce it. *(ADR-0015 dec 4 rev 1, rev 3;
instance: ADR-0030 dec 3)*

**SQL proxy** — the mediator a notebook's queries pass through; it redeems the SQL-audience handle
and opens each database session as the caller's tenant's own login identity, which can reach nothing
outside that tenant whatever the kernel sends, and it re-checks the handle while the session stays
open, closing it once the handle is revoked. The proxy's own database identity holds no tenant's role,
and the kernel never holds a DB credential. *(ADR-0012 dec 4 rev 2; ADR-0015 dec 2, 5-6, rev 2-3)*

**Per-tenant read-only role** — the **standing**, per-tenant database principal whose grants
reach one tenant's schema, read-only. It is shared by that tenant's sessions and reached only
through the SQL proxy, which logs in as the tenant's own login identity — a member of this role and
of no other tenant's — so the confinement is a property of who is logged in, not of a statement on
the session. The per-session, short-lived property belongs to the capability handle, not the role.
Why a role switch alone is not enough is ADR-0015 rev 2; names and grants are owned by the database
init scripts, not this file. *(ADR-0011 dec 3, 6; ADR-0012 dec 4 rev 2; ADR-0015 dec 5)*

---

## Models

**Model artifact path** — the journey a model makes from the notebook that trains it to the service
that scores with it: authoring kernel → tracking gateway → registry → `ml_service`. What crosses is a
**serialised model, not data**, which is why the two ends are constrained. It is *not* a pickle,
though it is routinely assumed to be: sklearn models cross as **skops** and xgboost models as a
**native booster** (ADR-0027 gives the detail — do not re-derive it from the shape of the code, which
is how it has been got wrong before). *(ADR-0027; the hops themselves are ADR-0019 and ADR-0010 dec 6)*

**Train/serve parity** — the constraint on that path: **the artifact declares its requirements, and
the serving environment satisfies them or refuses the model.** The rule is generic — the artifact is
the authority — so it needs no amendment when a new framework appears. The per-library version
constraints are an *instantiation* of it for the flavors served today, not the rule itself; they are
a **floor on divergence, not a pin**. *(ADR-0027 dec 1 and 3; do not restate the pins elsewhere)*

**Supported flavor set** — what the serving environment can actually honour: the union of what
`ml_service` carries in-process and what **model adapters** are installed. It is **closed and
declared, while the set users may author in is open** — a model outside it is *refused with a
reason*, never silently mis-served. *(ADR-0027 dec 2)*

**Model adapter** — how a model flavor becomes servable: a detector registered with the **score
semantics** it implements and the artifact flavors it can score. Named as a plugin category by ADR-0008
dec 3; its contract was deferred by ADR-0010 and is **written by ADR-0028**. In-process is the default
(ADR-0008); a framework this image does not carry — torch today — is **refused at the gate with a
reason**, and the out-of-process serving boundary it would need is ADR-0028's own named deferral.
*(ADR-0028)*

**Score semantics** — what a model's output MEANS: `anomaly_probability`, `outlier_score`,
`reconstruction_error`, or `direct_score`. **Declared by the detector, never inferred from the output**, and fixed per model when it is
registered, by recording its detector (ADR-0028 dec 1 rev 1) —
it cannot be inferred, because the information is not there: `predict() == 1` means *normal* to an
IsolationForest and *anomaly* to an XGBoost classifier, and an autoencoder emits no verdict at all. A
model whose semantics cannot be established is **refused, not scored** — a confident wrong score is worse
than an honest refusal, because ADR-0021 raises alerts on these numbers. *(ADR-0028 dec 1-2; the bug that
proved it is #236)*

**Provenance** — where a model was authored: inside the platform's own environment, logged
through it, or outside it and uploaded. Recorded as a first-class fact derived from how the model
arrived, never accepted from the caller and never synthesised to look internal; the serving side
grants trust in what an artifact deserialises into by provenance, never by what the artifact
claims. *(ADR-0030 dec 8; ADR-0027 dec 13)*

**Compatibility status** — a property of a registered model recording whether the serving environment
still satisfies what the model declares. It is **surfaced, never alerted** — deliberately not routed
into ADR-0022's `retrain_recommended`, which is a statistical claim about the world, not a mechanical
one about two containers. *(ADR-0027 dec 7-8)*

---

## Deployment

The self-hosted/managed split is a **business and licensing** distinction (ADR-0001). It maps
onto two **deployment shapes** (ADR-0009 rev 1) — do not attribute the deployment mapping to
ADR-0001.

**Self-hosted** — a customer running the platform on their own infrastructure. Has **two
supported shapes**: **single-host Docker Compose** (small / test) and **in-cluster Helm /
Kubernetes** (scaled). *(ADR-0001 for the model; ADR-0009 rev 1 for the shapes)*

**Managed (commercial)** — IndustryFlow operating the platform as a service for tenants. Always
**Kubernetes / Helm**, typically pointing at external managed datastores. *(ADR-0001, ADR-0009)*

**Compose deployment** — `docker-compose.yml` is both the local-development path **and** a
supported single-host self-hosted deployment target. It forgoes the Kubernetes-only orchestration
primitives (rolling updates, autoscaling, the Ingress TLS edge, StatefulSets) by design.
*(ADR-0009 rev 1 dec 6)*

**Box run / box-validated** — a human validation of a merged commit on the long-lived compose
deployment, the only check that exercises accumulated state (real data, configuration drift, an
operator's actual volumes). *Box-validated* means a change has passed one. It is post-merge
discovery, deliberately **not** a merge gate; what `main` means is set by the required checks.
*(ADR-0026 dec 2, 6)*

**`inCluster` toggle** — the per-dependency Helm switch that runs stateful infrastructure
in-cluster (self-hosted default) or points at an external endpoint (managed default), so one
chart serves both deployment models without a fork. *(ADR-0009 dec 3)*

---

## ADR vocabulary

The relationship fields in an ADR's metadata header. (Their consistent use is itself an
ADR-0000 concern.)

**ADR** — Architecture Decision Record: one decision and its rationale, owning the *why* while
downstream artifacts own the *what*. Files are `ADR/ADR-NNNN-kebab-case-title.md`. *(ADR-0000)*

**Parent** — the ADR this one is decided *under*: either the framing root (ADR-0001) or a
broader ADR this one refines. A child names its Parent; roots do not enumerate their children.

**Companion** — a peer ADR addressing an adjacent concern at the same level. Treated as an
**advisory, not strictly symmetric** pointer: a later ADR commonly lists an earlier foundational
ADR as a Companion without the earlier one being edited to point back. Do not read a missing
back-reference as an error. (Strict reciprocity is *not* enforced — see the cross-reference
audit.)

**Refined by** — names a later ADR that makes this one's decision more concrete or adjusts it
without overturning it (e.g. ADR-0011 *Refined by* ADR-0018 and ADR-0015). The refining ADR names
this one in its own metadata — as its **Parent** when it is decided under it (ADR-0018 under
ADR-0011), or as a **Companion** when it is decided under the framing root and refines a peer
(ADR-0015, whose Parent is ADR-0001) — so the link is reciprocal, though not always through the
same field.

**Supersedes** — names the prior decision (often a prior **revision** of the same ADR) that this
one replaces. A substantive change to a recorded decision produces a new revision: a `(rev N)`
title plus a `Supersedes:` line, with the reason woven into *Context and problem* and
*Alternatives considered*, per ADR-0000 dec 5. The `Status` field carries only the lifecycle state
of ADR-0000 dec 7 (`Proposed`, `Accepted`, `Superseded`) — never revision history or
implementation progress. *(ADR-0000 dec 5, 7)*

**Related (sibling)** — a reference to the **sibling project's** ADRs, not this repository's. The
"(sibling)" qualifier is load-bearing: the sibling's ADR numbers overlap this repo's (both have an
ADR-0001, ADR-0007), so the qualifier is the only thing disambiguating them. *(e.g. ADR-0002,
ADR-0007, ADR-0008)*

**Single source of truth** — the governing discipline: every fact has exactly one authoritative
home and is referenced, never copied, elsewhere. This glossary follows it by pointing at the
owning ADR rather than restating its decision. *(ADR-0000)*
