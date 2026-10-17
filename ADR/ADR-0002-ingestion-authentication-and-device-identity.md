<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0002 — Ingestion authentication and device identity: mTLS for devices, JWT for the API

- **Status:** Accepted
- **Date:** 2026-06-26; changed 2026-10-17
- **Parent:** [ADR-0001](ADR-0001-industryflow-framing.md)
- **Related:** [ADR-0003](ADR-0003-tenant-to-schema-resolution.md) (consumes the verified tenant),
  [ADR-0004](ADR-0004-api-authentication-sessions-and-transport.md) (the API trust domain),
  [ADR-0007](ADR-0007-public-key-infrastructure.md) (the device PKI)

## Context

Sensor data comes from unattended gateways holding a hardware-bound key in a secure element. A bearer
token suits them badly: nobody logs in, whoever holds it can replay it, and a tenant taken from it or
from the request body is caller-asserted. The tenant a write lands in is a security boundary, on the
highest-throughput path.

## Decision

1. **Device ingestion authenticates with mutual TLS; the HTTP API keeps JWT.** The two trust domains
   keep two mechanisms.
2. **mTLS terminates at the edge proxy, not in the application.** The edge verifies the client
   certificate and forwards the verified identity in request headers.
3. **The ingestion service trusts identity headers only from its edge, under exactly one convention.**
   The service is reachable only from the edge and reaches only the broker; each deployment names its
   edge's header convention with no default, and refuses a request carrying another convention's
   headers, because an edge overwrites only the headers it writes.
4. **IndustryFlow operates its own certificate authority for device certificates.** Hierarchy,
   issuance, lifetime and revocation are ADR-0007.
5. **Tenant and device identity are read from the verified certificate.** Never from the request body
   or a token claim.
6. **The ingested tenant is validated as a UUID before it routes a write.**
7. **Reference and test producers authenticate with test certificates.** No standing secret is
   committed to source or baked into an image.
8. **Ingestion accepts only mTLS and holds no database role.** It authenticates the device and
   produces to the broker.

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| 1 | JWT bearer tokens for device ingestion | Replayable, not bound to the hardware key, and needs a login no gateway can perform |
| 1 | Per-device API keys | Still a replayable bearer secret, with its own issuance and revocation beside a CA |
| 2 | mTLS verified inside the ingestion service | Trust-store handling and rotation in every replica, and cryptography in application code |
| 3 | Honouring every header convention a supported edge might write | A convention the edge does not write is not overwritten, so a client-supplied identity passes as verified |
| 5 | A tenant in a header or body without certificate binding | A caller-asserted tenant routes the write |
| 6 | Resolving the tenant by scanning every tenant schema per request | A per-request cost that grows with tenants, fed by a caller-supplied value |
| 8 | mTLS for every producer at once, with no transition | Forces every existing producer onto PKI in one step, with no migration path |
| 8 | JWT ingestion accepted beside mTLS while producers migrate | Two mechanisms widen the surface once every producer presents a certificate |
| 8 | A database role for the ingestion service | A standing credential with no use once the tenant comes from the certificate |

## Consequences

**Positive**
- Device credentials are hardware-bound and not replayable; the tenant needs no lookup.
- Test producers exercise the real authentication path.

**Negative**
- The platform operates a CA: issuance, provisioning, rotation, revocation.
- Isolating the service behind its edge is a standing invariant; a wrong header convention refuses
  every device.
- Local development needs a dev CA, certificates and the edge proxy.

## Deferred

| Open question | Settled by |
|---|---|
| Internal service-to-service authentication | An ADR alongside ADR-0004, when an internal call carries tenant authority beyond the interim mechanism |

## References

- `services/ingestion_edge/`, `services/ingestion_service/`: the edge and identity parsing.
- `docker-compose.yml`, `deploy/helm/`: the isolation of decision 3.
