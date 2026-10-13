<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0017: Database TLS and authentication (rev 1)

- **ID:** ADR-0017
- **Status:** Accepted
- **Date:** 2026-06-27 (rev 1: 2026-06-27, recorded 2026-10-13)
- **Project:** IndustryFlow
- **Parent:** ADR-0001 (framing)
- **Companions:** ADR-0004 (API edge TLS — in the self-hosted deployment its edge cert shares the internal CA this DB cert uses), ADR-0007 (device PKI — referenced for the signing pattern; its device CA is a *separate* root, not this one), ADR-0003 (per-tenant schemas), production-readiness item 4
- **Supersedes:** ADR-0017 rev 0 (2026-06-27 — decided the database certificate for the single-host deployment only; rev 1 adds decision 7, how the Kubernetes deployment sources and distributes it)

## Context and problem

TimescaleDB is the system of record, and every service authenticates to it with a password
over a plain TCP connection: `pg_hba.conf` uses `host ... md5` and the clients set no
`sslmode`. Connections are already scoped to private (RFC1918) networks and host ports are
bound to loopback (ADR-0009 hardening), but the traffic — credentials and tenant data — is
**unencrypted on the wire**, and a client cannot tell whether it is really talking to our
database (no server authentication). Two weaknesses remain: passive sniffing of an internal
network segment, and an active MITM / a rogue process impersonating the DB. `md5` password
hashing is also deprecated in favour of SCRAM.

**Revision 1 (2026-06-27; recorded 2026-10-13).** Rev 0 decided the database certificate for the single-host deployment, where the certificate can be issued beside the database at start. The Kubernetes deployment cannot do that: its clients are separate workloads that must all trust the same root, so the certificate and its root must exist before any of them start. Decision 7 answering that was added to this record after acceptance by an in-place edit; this revision records it as the decision it is.

## Decision drivers

- **System of record.** DB traffic carries credentials and all tenant data; best practice for
  it is encryption **and** server authentication, not encryption alone.
- **An internal CA already exists** — the self-hosted deployment already runs an internal CA that
  issues the server certificate for the **frontend TLS edge** (the
  internal-CA path ADR-0004 dec 8 decides for the self-hosted edge, as distinct from the
  public ACME cert used by the managed edge). Reusing that same internal root for the DB server
  cert means self-hosted clients trust **one internal CA for both the API edge and the
  database**. This is *not* ADR-0007's device CA: that is a separate client-auth root that signs
  CSRs and never holds a device's private key; it is referenced here only as the PKI precedent.
- **The change is cross-cutting and breaking if rushed.** Turning TLS on needs a database
  restart, every client driver in the platform must opt in, and requiring TLS rejects any client
  still on plaintext — so it must roll out clients-first, then enforce.
- **Postgres is strict about private-key file ownership and permissions**, which host bind mounts
  and orchestrator secret mounts do not satisfy by default.

## Decision

1. **Clients verify the server, not merely encrypt.** Every client connects with full
   verification: the connection is encrypted **and** the server certificate is checked against
   the internal CA, hostname included. Encrypt-only was rejected — it leaves MITM and
   impersonation open, and the CA to verify against already exists.

2. **Password authentication uses SCRAM, not MD5.** Roles carry SCRAM verifiers and the database
   accepts only the SCRAM method, retiring the deprecated MD5 hashing.

3. **The database server certificate is issued by the self-hosted internal CA,** with subject
   names covering every hostname clients use to reach the database, so full verification passes.
   The CA's root is distributed to clients as their trust anchor for the database.

4. **Key ownership and permissions are fixed inside the deployment, not on the host.** A one-shot
   step that runs before the database copies the certificate and key into database-owned storage
   with the permissions Postgres requires, so the strict key check is satisfied reproducibly
   without host root, in both deployment shapes.

5. **The rollout is staged so that no step breaks a running client:** first the server offers TLS
   without requiring it; then every client switches to full verification while plaintext is still
   accepted; only then does the server require TLS and SCRAM, rejecting plaintext. Each stage is
   validated before the next. (The stage-by-stage procedure is in `docs/operations/tls.md`.)

6. **Scope.** The database's local socket path, used only by initialisation inside the database
   container, stays plaintext — it never crosses the network. The mTLS *device* edge
   (ADR-0002) and the API HTTPS edge (ADR-0004) are unchanged; this ADR is about
   service→database transport.

7. **On Kubernetes the certificate comes from a cluster secret, provisioned by a certificate
   controller or supplied by the operator *(rev 1)*.** Clients there are separate workloads that
   must all trust the *same* root, so the certificate cannot be self-issued at database start as on
   a single host. The database mounts the certificate, key and root from one secret; every client
   mounts only the root; the verification mode and root location reach clients through shared
   configuration. The chart can self-provision that secret with a certificate controller (a
   self-signed issuer → internal CA → server certificate, the in-cluster equivalent of the
   single-host internal CA), or the operator supplies one from their own PKI. Because the hardened
   access rules require TLS, database TLS is **mandatory** for an in-cluster database, not optional;
   every role that connects over the network, monitoring included, is held to the same rule.

## Alternatives considered

- **Encrypt-only.** Simpler (no CA distribution), but no server
  authentication — rejected per driver 1.
- **mTLS client certificates to the DB** instead of passwords.
  Stronger, but a large per-service cert-issuance/rotation burden; deferred. SCRAM over
  `verify-full` server TLS is the chosen balance.
- **Leave it plaintext, rely on network scoping.** Rejected: defence-in-depth for the system
  of record; network controls alone don't stop an on-network MITM or sniffer.
- **On Kubernetes, issue the certificate at database start as on a single host *(rev 1)*.**
  Rejected: clients are separate workloads that need the root before the database exists to hand
  it to them; a root minted inside the database pod has no way to reach them that is not a second,
  ad hoc distribution channel. A cluster secret is the orchestrator's own distribution channel.

## Consequences

### Positive

- All service→DB traffic is encrypted and the server is authenticated; credentials never cross
  the network in the clear, and SCRAM replaces MD5.
- Both deployment shapes share one mechanism: one canonical set of database access rules and the
  same in-deployment key-permission fix.

### Negative

- Every client carries the CA root and a verification setting; certificate rotation goes through
  the internal CA (reissue the server certificate, restart the database).
- A failed certificate, permission or rotation breaks database connectivity, so changes are staged
  and validated.
- Kubernetes adds an init step for key permissions.
- The chart requires a database-certificate secret (controller-provisioned or operator-supplied);
  an install without one leaves the database and its clients pending — the chart says so at
  install time.

## References

- ADR-0004 — the API/frontend TLS edge whose self-hosted internal CA this database certificate
  shares; the managed edge instead uses a public ACME certificate. ADR-0007 — the *device* CA; a
  separate root, referenced for the signing pattern, not reused here.
- `docs/operations/tls.md` — how the certificate is issued in each deployment shape, the
  configuration names, the staged rollout procedure, and rotation.
- `docs/operations/production-readiness.md` — item 4 (database TLS).
