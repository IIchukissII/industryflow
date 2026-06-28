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

**Tenant** — one customer organisation on the platform. Identified by `company_id`, a UUID.
"Tenant", "company", and `company_id` refer to the same thing; prefer "tenant" in prose and
`company_id` for the concrete identifier. *(ADR-0001, ADR-0003)*

**Schema-per-tenant** — the isolation model: each tenant's data lives in its own PostgreSQL
schema named `tenant_<company_id>` (UUID hyphens replaced by underscores), not in shared tables
with a tenant column. *(ADR-0003)*

**Tenant-to-schema resolution** — mapping a verified caller's `company_id` to its
`tenant_<…>` schema through one validated, parameterised implementation — *not* the
scan-all-schemas, string-spliced lookup the resolution ADR replaced. *(ADR-0003)*

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
requests for ingestion mTLS. It signs CSRs and **never holds a private key**. It authenticates
devices *to* the platform. It is **not** used for any server certificate. *(ADR-0007)*

**Public edge certificate (managed)** — the browser-facing API/frontend server certificate in
the **managed** deployment: a **publicly-trusted ACME / Let's Encrypt** certificate. It
authenticates the platform *to* browsers. A distinct PKI concern from the device CA.
*(ADR-0004 dec 8)*

**Internal edge+DB CA (self-hosted)** — the **internal** CA used in the **self-hosted / Compose**
deployment, where a public ACME cert is not obtainable for an internal hostname (e.g.
`industryflow.local`). It is the server-certificate root for both the frontend TLS edge and
TimescaleDB. A separate root from the Device CA. For the decision (why one internal root serves
both edge and database, and the issuing script) see ADR-0017. *(ADR-0004 dec 8 keys the edge
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
many times (e.g. upsert by key), so at-least-once duplicates are harmless. *(ADR-0005, ADR-0006)*

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

**Capability handle** — an opaque, high-entropy token handed to a notebook kernel in place of a
database password. It is **per-session and short-lived**, and is redeemed at the SQL proxy.
*(ADR-0012, ADR-0015)*

**SQL proxy** — the mediator a notebook's queries pass through; it redeems the capability handle
and assumes the caller's per-tenant read-only role, so the kernel never holds a DB credential.
*(ADR-0015)*

**Per-tenant read-only role** — a **standing** PostgreSQL `NOLOGIN` role, scoped to one tenant's
schema and assumed via `SET ROLE` by the SQL proxy. Standing and shared across a tenant's
sessions; the per-session, short-lived property belongs to the capability handle, not the role.
The role *name* (`tenant_reader_<uuid>`) is owned by the TimescaleDB init scripts
(`infrastructure/timescaledb/init-scripts/`), not coined here. *(ADR-0015 dec 5, ADR-0018)*

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
without overturning it (e.g. ADR-0011 *Refined by* ADR-0018 and ADR-0015). The refining ADR
declares this one as its **Parent**; this back-link is expected to be reciprocal.

**Supersedes** — names the prior decision (often a prior **revision** of the same ADR) that this
one replaces. A substantive change to a recorded decision produces a new revision: a `(rev N)`
title plus a `Supersedes:` line, per ADR-0000 dec 5. *(ADR-0000 dec 5)*

**Related (IndustryGrow)** — a reference to the **sibling project's** ADRs, not this
repository's. The "(IndustryGrow)" qualifier is load-bearing: IndustryGrow's ADR numbers overlap
this repo's (both have an ADR-0001, ADR-0007), so the prefix is the only thing disambiguating
them. *(e.g. ADR-0002, ADR-0007, ADR-0008)*

**Single source of truth** — the governing discipline: every fact has exactly one authoritative
home and is referenced, never copied, elsewhere. This glossary follows it by pointing at the
owning ADR rather than restating its decision. *(ADR-0000)*
