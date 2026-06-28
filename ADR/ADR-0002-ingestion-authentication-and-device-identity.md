<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0002: Ingestion authentication and device identity — mTLS for gateways, JWT for the API

- **ID:** ADR-0002
- **Status:** Accepted
- **Date:** 2026-06-26
- **Project:** IndustryFlow
- **Parent:** ADR-0001 (framing)
- **Companions:** ADR-0003 (tenant→schema resolution), ADR-0004 (API authentication, sessions & transport)
- **Related (IndustryGrow):** ADR-0004 rev 1 (gateway host hardening), ADR-0007 (PKI and secure-element identity)

## Context and problem

IndustryFlow's ingestion endpoint authenticates every caller with a **JWT bearer token** and then resolves the caller's tenant by scanning the database: it lists all `tenant_%` schemas and, for each, issues `SET search_path` plus `SELECT company_id FROM "user"` until one matches (`services/ingestion_service/dependencies.py:53-73`). The reference data producer ships a **hardcoded, now-expired JWT** committed to source (`services/mock_service/stream_tep_data.py:19`), so today the streamer authenticates with a credential anyone can read and that no longer works.

This model was designed for a human or a service calling an HTTP API, and it is the wrong fit for the producers IndustryFlow actually has to serve. The platform's first real deployment is IndustryGrow, whose sensor data originates on **unattended field gateways** — Raspberry-Pi-class devices that hold a hardware-bound identity in an ATECC secure element and authenticate by PKI, not by logging in (IndustryGrow ADR-0004 rev 1, ADR-0007). A bearer JWT does not match that producer on three counts:

- **There is no human to log in.** A field gateway cannot complete an interactive login flow to mint a token, and a long-lived token minted out-of-band becomes a static secret on the device.
- **A bearer token is replayable.** Whoever holds the JWT *is* the device, for the token's whole lifetime (currently seven days). The credential is not bound to the device's key; a copy works from anywhere. The committed mock token is the degenerate case of this.
- **The tenant is derived from an untrusted source and used unsafely.** `company_id` reaches the ingestion path from the token/body and is interpolated into `SET search_path TO {schema}` without UUID validation (review finding **X1**, the `search_path` injection), and resolving it costs an O(number-of-tenants) schema scan on the hot ingestion path (review finding **X2**).

So ingestion authentication is really **two different trust domains wearing one mechanism**. Machine producers in the field need a credential bound to hardware that cannot be replayed and that carries a *verified* tenant identity; human and service callers of the HTTP API (the frontend, operators, service-to-service calls) are well served by the existing JWT. Forcing both through one JWT path is what produces the committed-secret footgun, the replay exposure, and the unsafe tenant derivation. This ADR records how ingestion authenticates, and where the tenant identity for ingested data comes from. It does **not** redesign the API's JWT auth (ADR-0004) nor the general tenant→schema resolution used by query paths (ADR-0003); it decides the ingestion/device boundary and hands those neighbours a verified identity to build on.

## Decision drivers

- **Device credentials must be bound to the device and non-replayable.** A field gateway's identity should live in its secure element and be unusable if copied off the device — the property a bearer token cannot have and mTLS client authentication does.
- **Tenant identity must come from a verified source, never from untrusted input.** The schema a write lands in is a security boundary; deriving it from a request body or an unvalidated claim and splicing it into SQL is the X1 defect. A cryptographically verified certificate field is a trustworthy source.
- **Tenant resolution on the ingestion hot path must be O(1), not O(tenants).** The per-request schema scan (X2) does not belong on the highest-throughput endpoint in the system.
- **Existing API clients must keep working.** The frontend and operators authenticate with JWT today; the device decision must not break the human/service API.
- **Cryptographic termination should stay out of application code.** Certificate verification, rotation, and revocation are better handled by infrastructure built for it than re-implemented inside the FastAPI app.
- **Align with the IndustryGrow gateway, do not reinvent it.** IndustryGrow already decided hardware identity and PKI for its gateways (ADR-0004 rev 1, ADR-0007). IndustryFlow's ingestion should be the server side of that same trust model, not a parallel one.
- **No standing secrets in source or images.** The committed mock JWT must have no successor; test producers must authenticate the same way real ones do, with test credentials that are not long-lived bearer secrets.

## Decision

### Two trust domains

1. **Device/gateway ingestion authenticates with mutual TLS (mTLS); the HTTP API keeps JWT.** Producers writing sensor data — field gateways and any machine producer — present a **client certificate** and are authenticated by it. Human and service callers of the API (frontend, operators, service-to-service) continue to authenticate with JWT bearer tokens as decided in ADR-0004. These are two distinct trust domains with two distinct mechanisms, deliberately not merged.

### Where mTLS terminates

2. **mTLS is terminated at the reverse proxy / ingress, not in the application.** A proxy (e.g. nginx/Traefik/Envoy — the specific product is an implementation choice, not fixed here) performs the TLS handshake, verifies the client certificate against IndustryFlow's CA, and forwards the request to the ingestion service over the trusted internal network with the **verified** certificate identity passed in trusted request headers (subject/SAN fields). The ingestion service does not perform TLS client-cert verification itself.

3. **The ingestion service trusts the identity headers only from the terminating proxy.** Because a verified-identity header is forgeable if it can reach the service from anywhere, the ingestion service must be reachable only via the proxy (network isolation), and must not accept the identity headers from any other source. The trust that the header reflects a *verified* certificate is the proxy's responsibility and the network's; this coupling is explicit and is called out in the negative consequences.

### Identity and the certificate authority

4. **IndustryFlow operates its own certificate authority and issues device certificates.** This CA is the server-side counterpart to IndustryGrow's gateway PKI (ADR-0007); IndustryFlow issues the client certificates that gateways present. The CA hierarchy, issuance/provisioning workflow, and revocation mechanism are PKI concerns deferred to a dedicated PKI ADR (see *Deferred decisions*) — this ADR fixes only *that* IndustryFlow runs its own CA and that ingestion trusts certificates chaining to it.

5. **Tenant and device identity are carried in the certificate.** A device certificate encodes its `company_id` (tenant) and a `device_id` in the subject/SAN. The exact field encoding is a PKI-ADR detail, but the decision here is that the **tenant is read from the verified certificate, not from the request body or a JWT claim**.

### Consequences for tenant resolution on ingestion

6. **Ingested data's tenant comes from the verified certificate field and is validated before use.** The `company_id` used to route a write to its tenant schema is taken from the authenticated certificate identity, validated as a well-formed UUID, and only then used to build the schema name. This **supersedes the ingestion path's body/JWT-derived `company_id`** and removes the X1 injection vector and the X2 per-request schema scan *for ingestion*. The general tenant→schema resolution mechanism shared by query paths is decided in ADR-0003; this ADR guarantees ADR-0003 a trustworthy, pre-validated tenant identity on the ingestion side.

### Test and reference producers

7. **Reference and test producers authenticate the same way, with test certificates.** The mock/reference streamer and integration tests present client certificates issued by a test CA, not bearer tokens. No long-lived JWT or other standing secret is committed to source or baked into an image; the committed mock JWT (`stream_tep_data.py:19`) is removed with no bearer-token successor.

### Transition

8. **The JWT ingestion path is retired for devices but removed only after producers are migrated.** Until the reference producer and any real gateways present certificates, the JWT ingestion path may remain accepted as a temporary, explicitly-labelled transition mechanism; it is removed once producers are on mTLS. This transition window is the only period in which both mechanisms accept ingestion, and it is a migration aid, not the end state.

   > **Transition closed (2026-06-27).** The reference producer now presents a client certificate (decision 7), so the JWT ingestion path and its database lookup have been removed: `get_ingestion_identity` accepts only a verified client certificate. As a consequence ingestion no longer holds a database role at all — it is stateless, authenticates by mTLS, and produces only to Kafka. The `ingestion_service_user` role, its `pg_hba` entry, and its per-tenant grants are removed, and a Kubernetes egress NetworkPolicy restricts the ingestion pod to Kafka + DNS (defense in depth against the shared-Secret credentials). The API's JWT trust domain (ADR-0004) is unaffected.

## Alternatives considered

**A. Keep JWT for ingestion (status quo).** *Rejected:* it is a replayable bearer credential ill-suited to unattended field devices, it is the source of the committed-secret footgun, and it derives the tenant from untrusted input spliced into SQL (X1/X2). None of these are fixable while the credential remains an unbound bearer token.

**B. mTLS for all ingestion, no JWT anywhere on the path.** *Rejected for now:* cleaner in principle, but it forces every ingestion client — including any current HTTP/JWT producer and test tooling — onto PKI on day one with no transition, and conflates the device decision with the API's auth model (ADR-0004). The dual model reaches the same end state for devices without breaking API clients; decision 8 keeps the transition bounded.

**C. Per-device API keys (long-lived bearer tokens issued per gateway).** *Rejected:* an API key is still a bearer secret — replayable if copied, not bound to the device's hardware key, and requiring its own issuance/rotation/revocation machinery that duplicates what a CA already provides. It would not let IndustryFlow reuse IndustryGrow's secure-element identity (ADR-0007); it reinvents a weaker version of it.

**D. Terminate mTLS inside the ingestion service (uvicorn client-cert verification).** *Rejected:* it couples certificate verification, trust-store management, and rotation into application code and into every replica, makes horizontal scaling and cert rotation harder, and puts crypto where it is easy to get wrong. Terminating at a purpose-built proxy keeps the app handling an already-verified identity (decision 2). The price — the proxy-trust coupling — is accepted and bounded by decision 3.

**E. Pass the tenant in a request header/body without certificate binding.** *Rejected:* this is the X1 defect with extra steps — an unverified, caller-supplied tenant identity used to route a write. The whole point of decisions 5–6 is that the tenant comes from a *verified* certificate, not from anything the caller can assert freely.

## Consequences

### Positive

- Device credentials become hardware-bound and non-replayable: a certificate's private key lives in the gateway's secure element, so a copied credential is useless. This is the property the committed mock JWT most visibly lacked.
- The ingestion tenant is read from a cryptographically verified certificate field and validated before use, closing the X1 `search_path` injection and removing the X2 per-request schema scan on the highest-throughput endpoint.
- IndustryFlow becomes the coherent server side of IndustryGrow's gateway trust model (ADR-0004/0007) rather than a second, parallel scheme.
- No standing bearer secret remains in source or images; test producers exercise the same authentication path as real ones, so the test setup validates the production mechanism.
- The API's existing JWT auth is untouched, so the frontend and operators keep working through the change.

### Negative

- IndustryFlow must run and operate a certificate authority — issuance, provisioning onto devices, rotation, and revocation — which is real operational burden that did not exist under JWT. The bulk of it is deferred to the PKI ADR, but the burden is incurred by this decision.
- The ingestion service now trusts an identity header from the terminating proxy; if the service is reachable off the proxy path, that header is spoofable. The deployment must enforce that the ingestion service is reachable only via the proxy (decision 3) — a network-isolation requirement that becomes a standing operational invariant.
- Local development and testing get more complex: a dev CA, dev certificates, and the terminating proxy must be present to exercise the real ingestion path.
- During the transition window (decision 8) two ingestion auth mechanisms were accepted at once, a temporary increase in surface area. This window is now **closed** (see decision 8): ingestion accepts only mTLS and holds no database role.

## Deferred decisions

- **PKI architecture.** CA hierarchy (root/intermediate), the exact certificate fields and encoding for `company_id`/`device_id`, device provisioning and enrolment workflow, certificate lifetime, and revocation mechanism (CRL/OCSP) are a dedicated PKI ADR — mirroring IndustryGrow's split between gateway behaviour (ADR-0004) and PKI/identity (ADR-0007). This ADR fixes only that IndustryFlow runs its own CA and reads tenant/device identity from the certificate.
- **Reverse-proxy product and configuration.** The choice of nginx/Traefik/Envoy and the concrete header names carrying the verified identity are implementation concerns owned by the deployment artifacts, not this ADR.
- **General tenant→schema resolution.** How the validated `company_id` maps to a schema and is applied safely on *all* paths (not just ingestion) is ADR-0003; this ADR supplies it a verified identity for the ingestion case.
- **Service-to-service auth.** Whether internal service-to-service calls (gateway→alert, etc.) use mTLS, JWT, or network trust is out of scope here and belongs with ADR-0004.

## References

- IndustryFlow review (2026-06-26), findings X1 (`search_path` injection) and X2 (per-request tenant-schema scan), and the committed mock JWT — internal report.
- IndustryGrow ADR-0004 rev 1 — gateway host hardening and stateless-edge operation (the device whose data IndustryFlow ingests).
- IndustryGrow ADR-0007 — PKI and secure-element identity (the device-side counterpart to IndustryFlow's CA).
- ADR-0003 — tenant→schema resolution, consumer of the verified identity decided here.
- ADR-0004 — API authentication, sessions & transport, the JWT trust domain this ADR leaves in place.
