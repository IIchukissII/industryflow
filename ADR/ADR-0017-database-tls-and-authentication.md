<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0017 — Database TLS and authentication

- **Status:** Accepted
- **Date:** 2026-06-27; changed 2026-10-17
- **Parent:** [ADR-0001](ADR-0001-industryflow-framing.md)
- **Related:** [ADR-0004](ADR-0004-api-authentication-sessions-and-transport.md) (edge TLS and its
  internal CA), [ADR-0007](ADR-0007-public-key-infrastructure.md) (device PKI, a separate root),
  [ADR-0009](ADR-0009-kubernetes-deployment-and-packaging.md) (deployment shapes)

## Context

The database carries credentials and every tenant's data. Network scoping does not stop an on-network
sniffer or an impersonator, and encryption without server authentication leaves impersonation open.
PostgreSQL rejects private keys with loose ownership or permissions, which host and orchestrator mounts
do not satisfy by default. Requiring TLS breaks any client not yet configured for it.

## Decision

1. **Clients verify the server, not merely encrypt.** Every network client checks the server
   certificate against its trust anchor, hostname included.
2. **Password authentication uses SCRAM only.** MD5 is not accepted.
3. **The server certificate is issued by the self-hosted internal CA where it is in use; a single host
   started without it self-signs.** Either way the certificate names every client-facing hostname and
   its root is every client's trust anchor; a self-signed certificate is kept across restarts and
   yields to a CA-issued one once supplied.
4. **Key ownership and permissions are fixed inside the deployment, not on the host.** A one-shot step
   before the database stages the certificate and key into database-owned storage with the permissions
   PostgreSQL requires.
5. **TLS changes roll out without breaking a running client.** Server offers, clients verify, server
   requires, each stage validated first.
6. **Scope is service-to-database transport.** The in-container local socket used by initialisation
   stays plaintext; the device and API edges are unaffected.
7. **On Kubernetes the certificate comes from a cluster secret, provisioned by a certificate
   controller or supplied by the operator.** The database mounts certificate, key and root, each client
   only the root; TLS is mandatory for every network role, monitoring included.

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| 1 | Encrypt-only | Impersonation stays open |
| 1 | Plaintext behind network scoping | Does not stop an on-network sniffer or impersonator |
| 2 | Client certificates instead of passwords | Per-service issuance and rotation burden |
| 2 | MD5 password hashing | A deprecated hash where SCRAM is available to every client |
| 3 | The certificate always from the internal CA, so a single host without it does not start | No database until an operator acts, and plaintext is no fallback |
| 7 | On Kubernetes, issue at database start | Clients need the root before the database exists |

## Consequences

**Positive**
- Service-to-database traffic is encrypted and the server authenticated, in both deployment shapes.

**Negative**
- Every client carries a trust anchor; rotation means reissuing and restarting the database.
- A self-signed single host shares no root with the edge; an outside client must be handed it.
- Without a database-certificate secret the chart's database and clients stay pending.

## Deferred

| Open question | Settled by |
|---|---|
| Client-certificate authentication to the database | The maintainer, if SCRAM over verified TLS is judged insufficient |

## References

- [docs/operations/tls.md](../docs/operations/tls.md): issuance, configuration, rollout, rotation.
- `infrastructure/timescaledb/config/pg_hba.conf`, `deploy/helm/industryflow/templates/db-tls-certificate.yaml`.
