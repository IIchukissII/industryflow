<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# TLS & the internal CA

All external traffic is served over HTTPS, terminated at the frontend nginx (the TLS
edge — see **[ADR-0004](../../ADR/ADR-0004-api-authentication-sessions-and-transport.md)**). Out of the
box the edge generates a **self-signed** certificate so the stack works immediately, at
the cost of a browser "not trusted" warning.

For a trusted setup on a LAN, run a small **internal Certificate Authority**: a root CA you
install on your devices once, which signs the server certificate. After that
`https://industryflow.local` is trusted with no warnings.

## TLS options at a glance

| Mode | Trust | How |
| --- | --- | --- |
| Self-signed (default) | One-time per-site warning | Nothing — generated automatically into the `frontend-certs` volume |
| Internal CA (this guide) | Trusted after installing the CA once | `scripts/gen-internal-ca.sh` + `TLS_CERT_DIR` |
| Public CA (e.g. ACME/Let's Encrypt) | Trusted everywhere | Out of scope here; mount the issued `tls.crt`/`tls.key` via `TLS_CERT_DIR` |

## Set up the internal CA

Run on the host that serves the frontend (needs `openssl`):

```sh
# The host's primary IP is auto-added to the SAN, so usually just:
scripts/gen-internal-ca.sh

# To pin the SAN explicitly (extra names/IPs, or to override auto-detection), set CERT_SAN —
# include every name/IP clients use to reach the edge (replace <host-ip> with the server's IP):
CERT_SAN="DNS:industryflow.local,DNS:industryflow,DNS:localhost,IP:127.0.0.1,IP:<host-ip>" \
  scripts/gen-internal-ca.sh
```

This writes two git-ignored directories:

- `deploy/ca/` — `ca.key` (the CA signing key) and `ca.crt`. **Never mounted** into any
  container, so the network-facing edge can't reach the signing key.
- `deploy/certs/` — `tls.crt` / `tls.key` (served by nginx) plus a copy of `ca.crt` for
  distribution. This is the only directory mounted into the frontend.

The root CA is generated once and **reused** on later runs, so you only install `ca.crt`
on clients a single time; re-running the script just reissues the leaf certificate.

Point the frontend at it and restart:

```sh
echo "TLS_CERT_DIR=$(pwd)/deploy/certs" >> .env   # absolute path
docker compose up -d --build frontend
```

The entrypoint only self-signs when no certificate is present, so the mounted CA-signed
cert is used as-is.

## Install the CA root on clients (once)

Copy `deploy/certs/ca.crt` to each device and trust it:

- **Linux:** `sudo cp ca.crt /usr/local/share/ca-certificates/industryflow-ca.crt && sudo update-ca-certificates`
- **macOS:** Keychain Access → *System* → drag in `ca.crt` → set it to *Always Trust*.
- **Windows:** `certutil -addstore -f Root ca.crt` (elevated), or *Manage computer certificates* → *Trusted Root Certification Authorities*.
- **Firefox** (uses its own store): Settings → Privacy & Security → *Certificates* → *View Certificates* → *Authorities* → *Import* → trust for websites.

Then browse to **https://industryflow.local** — no warning.

## Reaching it by name

`industryflow.local` is published over mDNS by `avahi-daemon` on the server (pinned to the
LAN interface) when the host's name is `industryflow`. macOS and Windows 10+ resolve `.local`
names natively; Linux clients need `avahi`/`nss-mdns`. If a device can't resolve it, use the
host's IP (which is in the cert SAN) — `https://<host-ip>` — or add a `hosts` entry
(`<host-ip> industryflow.local`).

## Renewing / rotating

- **Reissue the server cert** (e.g. add a SAN, or before it expires): re-run the script and
  restart the frontend. The CA is unchanged, so clients need no action.
- **Rotate the CA** (compromise, or a fresh trust anchor): delete `deploy/ca/`, re-run the
  script, and reinstall the new `ca.crt` on every client.

## Database TLS (ADR-0017)

The decision — full server verification, SCRAM, the internal CA as the database's root, a staged
rollout — is [ADR-0017](../../ADR/ADR-0017-database-tls-and-authentication.md). This section is
how it is realised.

### Compose (single host)

- `scripts/gen-internal-ca.sh` also issues the database server certificate, `db.crt` / `db.key`,
  from the same internal CA as the edge. Its SAN covers the names clients use
  (`DB_CERT_SAN`, default `timescaledb`, `localhost`, `industryflow.local`, `127.0.0.1`).
- The `db-cert-init` one-shot copies the certificate and key into a postgres-owned volume with
  mode `0600` before TimescaleDB starts — Postgres refuses a key with looser permissions.
- Clients read `DB_SSLMODE` (default `verify-full`) and `DB_SSLROOTCERT` (the mounted `ca.crt`).
- `pg_hba.conf` (one canonical file, shared with the chart) requires `hostssl` with
  `scram-sha-256`; the in-container Unix socket (`local … trust`, used by the init scripts) is the
  only plaintext path.

### Kubernetes (Helm)

- The chart consumes a Secret holding `tls.crt` / `tls.key` / `ca.crt`, named by
  `tls.db.secretName`. The TimescaleDB pod mounts all three (an initContainer stages the key with
  `0600` and the postgres uid); every database client mounts only `ca.crt`.
- `DB_SSLMODE` / `DB_SSLROOTCERT` come from the shared ConfigMap.
- With `tls.db.certManager.enabled` the chart provisions the Secret itself through cert-manager
  (`templates/db-tls-certificate.yaml`: a SelfSigned issuer → internal CA → server certificate).
  Otherwise the operator supplies the Secret from their own PKI. Without it the database and its
  clients stay pending, and `NOTES.txt` says so.
- The monitoring role (`metrics_user`, used by postgres-exporter) has its own `hostssl` rule.

### Staged rollout (how TLS was turned on without an outage)

1. **Server capable.** `ssl=on` with the certificate; `pg_hba` stays `host`, so TLS is offered
   but not required.
2. **Clients verify.** Every client sets `sslmode=verify-full` and `sslrootcert`; still accepted
   under `host`, now over TLS.
3. **Enforce.** `pg_hba` `host` → `hostssl` and `md5` → `scram-sha-256`; plaintext is rejected.

Validate each stage before the next. The compose path is enforced and validated on a live
deployment; the Helm path is render-validated (`helm lint` / `helm template`).

### Rotating the database certificate

Re-run `scripts/gen-internal-ca.sh` (the CA is reused, so clients need no change) and restart
TimescaleDB. On Kubernetes, renew or replace the Secret and restart the database pod.
