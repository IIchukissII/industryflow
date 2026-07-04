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
