<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# TLS and the internal CA

Server certificates for the HTTPS edge and for TimescaleDB, issued by the internal edge+DB CA.
Device client certificates are a separate PKI ([device mTLS](device-mtls.md)).

| Decision | ADR |
|---|---|
| The edge terminates TLS | [ADR-0004](../../ADR/ADR-0004-api-authentication-sessions-and-transport.md) |
| Database TLS, one internal root for edge and database | [ADR-0017](../../ADR/ADR-0017-database-tls-and-authentication.md) |

## Edge TLS modes

| Mode | Client trust | Setup |
|---|---|---|
| Self-signed (default) | Browser warning per site | None; the frontend entrypoint self-signs into the `frontend-certs` volume. Extra SANs: `CERT_EXTRA_SAN` (e.g. `IP:<host-ip>`) |
| Internal CA | Trusted after installing `ca.crt` once per client | `scripts/gen-internal-ca.sh`, then `TLS_CERT_DIR` |
| Public CA | Trusted everywhere | Place the issued `tls.crt` / `tls.key` in a directory and point `TLS_CERT_DIR` at it |

The entrypoint self-signs only when no certificate is present at `/etc/nginx/certs`.

## Issue certificates with the internal CA

Run on the Compose host (needs `openssl`):

```sh
scripts/gen-internal-ca.sh
echo "TLS_CERT_DIR=$(pwd)/deploy/certs" >> .env      # absolute path
docker compose up -d --build frontend ingestion-edge
```

| Name | Default | Meaning |
|---|---|---|
| `CA_OUT_DIR` | `deploy/ca` | CA key and certificate; never mounted into a container |
| `CERT_OUT_DIR` | `deploy/certs` | Served directory; the `TLS_CERT_DIR` target |
| `PRIMARY_HOST` | `industryflow.local` | Edge certificate CommonName |
| `CERT_SAN` | `DNS:industryflow.local,DNS:industryflow,DNS:localhost,IP:127.0.0.1` plus the host's primary IPv4 | Edge certificate SAN; setting it replaces the default, including the auto-detected IP |
| `DB_CERT_SAN` | `DNS:timescaledb,DNS:localhost,DNS:industryflow.local,IP:127.0.0.1` | Database certificate SAN |
| `CA_DAYS` | `3650` | Root CA validity |
| `CERT_DAYS` | `825` | Edge and database certificate validity |
| `NGINX_GID` | `101` | Group `tls.key` is assigned to, so the unprivileged nginx can read it |

| Output | Files | Mounted into |
|---|---|---|
| `deploy/ca/` | `ca.key` (mode `0600`), `ca.crt` | Nothing |
| `deploy/certs/` | `tls.crt`, `tls.key` (mode `0640`, group `NGINX_GID`) | `frontend`, `ingestion-edge` (server certificate) |
| `deploy/certs/` | `db.crt`, `db.key` (mode `0600`) | `db-cert-init` (staged for TimescaleDB) |
| `deploy/certs/` | `ca.crt` | Every database client (`DB_SSLROOTCERT`); distribute to browsers |

Both directories are git-ignored. The root CA is reused on later runs; only the leaf certificates are
reissued. If the script cannot `chgrp` the key, run `sudo chgrp 101 deploy/certs/tls.key`.

## Install the CA root on clients

Copy `deploy/certs/ca.crt` to each device.

| Client | Command or steps |
|---|---|
| Linux | `sudo cp ca.crt /usr/local/share/ca-certificates/industryflow-ca.crt && sudo update-ca-certificates` |
| macOS | Keychain Access → *System* → add `ca.crt` → *Always Trust* |
| Windows | `certutil -addstore -f Root ca.crt` (elevated) |
| Firefox (own store) | Settings → Privacy & Security → *View Certificates* → *Authorities* → *Import* → trust for websites |

## Name resolution

| Address | Requirement |
|---|---|
| `https://industryflow.local` | mDNS on the host (e.g. `avahi-daemon` with hostname `industryflow`); macOS and Windows 10+ resolve `.local` natively, Linux needs `nss-mdns` |
| `https://<host-ip>` | The IP is in the certificate SAN (auto-detected or in `CERT_SAN`) |
| Hosts file | `<host-ip> industryflow.local` |

## Rotation

| Task | Steps | Client action |
|---|---|---|
| Reissue the edge certificate (new SAN, expiry) | Re-run the script; `docker compose up -d --force-recreate frontend ingestion-edge` | None |
| Reissue the database certificate (Compose) | Re-run the script; `docker compose up -d --force-recreate db-cert-init`; `docker compose restart timescaledb` | None |
| Reissue the database certificate (Helm) | Renew or replace the `tls.db.secretName` Secret; restart the TimescaleDB pod | None |
| Rotate the CA | Delete `deploy/ca/`, re-run the script, recreate the edges and the database as above | Install the new `ca.crt` on every client; restart every database client |

## Database TLS

### Compose

| Element | Behaviour |
|---|---|
| `db-cert-init` (one-shot, runs on every `up`) | Copies `db.crt` / `db.key` / `ca.crt` from `TLS_CERT_DIR` into the `db-certs` volume, owned by uid 70, key `0600`. Without `TLS_CERT_DIR` it keeps an earlier staged certificate, else self-signs one. It publishes the staged CA to the `db-ca` volume |
| Client CA mount | `${TLS_CERT_DIR:-db-ca}` at `/etc/ssl/industryflow` (read-only) |
| Client settings | `DB_SSLMODE` (default `verify-full`), `DB_SSLROOTCERT=/etc/ssl/industryflow/ca.crt` |
| `pg_hba.conf` | `infrastructure/timescaledb/config/pg_hba.conf`, shared with the chart: `hostssl` + `scram-sha-256` on private ranges only; `local … trust` (Unix socket, init scripts) is the only non-TLS path. `metrics_user` (postgres-exporter) has its own `hostssl` rule |

### Helm

| Name | Default | Meaning |
|---|---|---|
| `tls.db.secretName` | `industryflow-db-tls` | Secret with `tls.crt` / `tls.key` / `ca.crt`. The TimescaleDB pod mounts all three (an initContainer stages the key `0600` for the postgres uid); every database client mounts only `ca.crt` |
| `tls.db.mountPath` | `/etc/ssl/industryflow` | Mount path; `DB_SSLMODE` / `DB_SSLROOTCERT` come from the shared ConfigMap |
| `tls.db.certManager.enabled` | `false` | The chart provisions the Secret through cert-manager (`templates/db-tls-certificate.yaml`: SelfSigned issuer → internal CA → server certificate). Requires the cert-manager CRDs |

Without the Secret and without cert-manager, the database and its clients stay pending; `NOTES.txt`
reports it.

### Staged enablement on an existing deployment

Each stage is validated before the next.

| Stage | Server | Clients |
|---|---|---|
| 1. Server capable | `ssl=on` with the certificate; `pg_hba` stays `host` | Unchanged |
| 2. Clients verify | Unchanged | `sslmode=verify-full` + `sslrootcert` |
| 3. Enforce | `host` → `hostssl`, `md5` → `scram-sha-256`; re-hash existing roles with `ALTER ROLE <role> PASSWORD '<same value>'` | Unchanged |
