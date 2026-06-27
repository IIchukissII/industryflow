<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0017: Database TLS and authentication

- **ID:** ADR-0017
- **Status:** Accepted
- **Date:** 2026-06-27
- **Project:** IndustryFlow
- **Parent:** ADR-0001 (framing)
- **Companions:** ADR-0004 (API transport TLS — same internal CA), ADR-0007 (PKI / internal CA), ADR-0003 (per-tenant schemas), production-readiness item 4

## Context and problem

TimescaleDB is the system of record, and every service authenticates to it with a password
over a plain TCP connection: `pg_hba.conf` uses `host ... md5` and the clients set no
`sslmode`. Connections are already scoped to private (RFC1918) networks and host ports are
bound to loopback (ADR-0009 hardening), but the traffic — credentials and tenant data — is
**unencrypted on the wire**, and a client cannot tell whether it is really talking to our
database (no server authentication). Two weaknesses remain: passive sniffing of an internal
network segment, and an active MITM / a rogue process impersonating the DB. `md5` password
hashing is also deprecated in favour of SCRAM.

## Decision drivers

- **System of record.** DB traffic carries credentials and all tenant data; best practice for
  it is encryption **and** server authentication, not encryption alone.
- **An internal CA already exists** (ADR-0007 / `gen-internal-ca.sh`). Reusing it for the DB
  cert means clients trust one root for both the API edge and the database.
- **The change is cross-cutting and breaking if rushed.** `ssl=on` needs a DB restart, four
  client drivers must opt in (asyncpg ×, SQLAlchemy+asyncpg, psycopg2, libpq/env), and
  flipping `pg_hba` to `hostssl` rejects any client still on plaintext — so it must roll out
  clients-first, then enforce.
- **Postgres is strict about key permissions** (key must be `0600`, owned by the DB user or
  root), which bind mounts and k8s Secret mounts don't satisfy by default.

## Decision

1. **`verify-full`, not encrypt-only.** Clients connect with `sslmode=verify-full` and
   `sslrootcert=<internal CA>`: the connection is encrypted **and** the server certificate is
   verified against the internal CA with hostname checking. Encrypt-only (`require`) was
   rejected — it leaves MITM/impersonation open, and the CA to do verification already exists.

2. **`scram-sha-256` for password auth.** Roles are created with SCRAM verifiers
   (`password_encryption = scram-sha-256`) and `pg_hba` uses the `scram-sha-256` method,
   replacing `md5`.

3. **Server certificate from the internal CA** (`gen-internal-ca.sh` issues `db.crt`/`db.key`),
   SAN = the hostname clients connect to (`timescaledb`, plus `localhost`/`industryflow.local`)
   so `verify-full` passes. The CA's `ca.crt` is distributed to clients as `sslrootcert`.

4. **Key permissions are fixed in-container, not on the host.** A `db-cert-init` one-shot
   (compose) / an **initContainer** (Helm) copies the cert into a postgres-owned volume with
   `0600`, so the strict key check is satisfied reproducibly without host root.

5. **Staged, non-breaking rollout** (each stage validated on the live box):
   - **A — server capable:** `ssl=on` + cert; `pg_hba` stays `host` (TLS offered, not required).
   - **B — clients verify:** every client sets `sslmode=verify-full` + `sslrootcert`; still
     accepted under `host`, now over TLS.
   - **C — enforce:** `pg_hba` `host`→`hostssl` and `md5`→`scram-sha-256`; plaintext now rejected.

6. **Scope.** The local Unix-socket path (`local ... trust`, used by the init scripts inside
   the container) stays plaintext — it never crosses the network. The mTLS *device* edge
   (ADR-0002) and the API HTTPS edge (ADR-0004) are unchanged; this ADR is about
   service→database transport.

7. **Helm cert source: a Secret, provisioned by cert-manager.** In k8s the server cert + CA
   cannot be self-signed at DB-pod start the way compose does it — clients are separate pods and
   must mount the *same* CA. So the chart consumes a Secret (`tls.crt`/`tls.key`/`ca.crt`,
   `tls.db.secretName`): the TimescaleDB pod mounts all three (the initContainer stages them with
   `0600`/postgres-uid), every DB client mounts just `ca.crt`, and `DB_SSLMODE`/`DB_SSLROOTCERT`
   come from the shared ConfigMap. The chart can self-provision that Secret with **cert-manager**
   (`tls.db.certManager.enabled` — a SelfSigned issuer → internal CA → server cert, the k8s
   equivalent of `gen-internal-ca.sh`), or the operator supplies one from their own PKI. Because
   the hardened `pg_hba` is `hostssl`, DB TLS is **mandatory** for an in-cluster TimescaleDB, not
   optional. The monitoring role (`metrics_user`, postgres-exporter) gets its own `hostssl` rule.

## Alternatives considered

- **Encrypt-only (`sslmode=require`).** Simpler (no CA distribution), but no server
  authentication — rejected per driver 1.
- **mTLS client certificates to the DB** (`clientcert=verify-full`) instead of passwords.
  Stronger, but a large per-service cert-issuance/rotation burden; deferred. SCRAM over
  `verify-full` server TLS is the chosen balance.
- **Leave it plaintext, rely on network scoping.** Rejected: defence-in-depth for the system
  of record; network controls alone don't stop an on-network MITM or sniffer.

## Consequences

- All service→DB traffic is encrypted and the server is authenticated; credentials never cross
  the network in the clear, and SCRAM replaces md5.
- Every client carries the CA (`sslrootcert`) and a `DB_SSLMODE` setting; cert rotation is via
  the internal CA (reissue `db.crt`, restart the DB).
- A failed cert/permission/rotation breaks DB connectivity, so changes are staged and validated.
- k8s adds an initContainer for key perms; both deployment models share one mechanism (the same
  canonical `pg_hba.conf` via the chart's `files/db-config` symlink).
- The Helm chart now requires a DB-cert Secret (cert-manager or operator-supplied); a plain
  `helm install` without it leaves the DB + clients pending — surfaced in `NOTES.txt`.
- The compose path is enforced and validated live; the Helm path is render-validated only (no
  cluster yet — the live deployment is docker-compose).

## References

- ADR-0007 — the internal CA this reuses. ADR-0004 — API transport (same CA).
- `scripts/gen-internal-ca.sh` — issues the DB server cert (compose); the Helm equivalent is
  `templates/db-tls-certificate.yaml` (cert-manager) under `tls.db` in `values.yaml`.
- `docs/operations/production-readiness.md` — item 4 (the remaining "DB TLS").
