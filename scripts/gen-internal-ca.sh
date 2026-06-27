#!/bin/sh
# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# Generate an internal Certificate Authority and a server certificate for the
# frontend TLS edge (ADR-0004 dec 1).
#
# The root CA is generated once and REUSED on subsequent runs, so you install it on
# client devices a single time; only the leaf server certificate is reissued. Install
# the resulting ca.crt as a trusted root on each client, point TLS_CERT_DIR at the
# output directory, and the frontend serves a trusted cert for industryflow.local.
#
# The CA private key is kept in a SEPARATE directory (deploy/ca) that is never mounted
# into the frontend container — only the leaf cert/key (deploy/certs) is served, so a
# compromise of the network-facing edge can't reach the CA signing key.
#
# Config (all optional, via environment):
#   CA_OUT_DIR    CA directory (private)      (default: <repo>/deploy/ca)
#   CERT_OUT_DIR  served cert dir (TLS_CERT_DIR target)  (default: <repo>/deploy/certs)
#   PRIMARY_HOST  cert CommonName             (default: industryflow.local)
#   CERT_SAN      subjectAltName list         (default: the local names below)
#   CA_DAYS       root CA validity in days    (default: 3650)
#   CERT_DAYS     server cert validity        (default: 825)
#
# The host's primary IP is auto-added to the SAN; override CERT_SAN to pin it explicitly.
# Example (replace <host-ip> with the server's IP):
#   CERT_SAN="DNS:industryflow.local,DNS:industryflow,DNS:localhost,IP:127.0.0.1,IP:<host-ip>" \
#     scripts/gen-internal-ca.sh
set -e

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
CA_DIR="${CA_OUT_DIR:-$ROOT/deploy/ca}"
OUT_DIR="${CERT_OUT_DIR:-$ROOT/deploy/certs}"
CA_DAYS="${CA_DAYS:-3650}"
CERT_DAYS="${CERT_DAYS:-825}"
PRIMARY_HOST="${PRIMARY_HOST:-industryflow.local}"
# Auto-include the host's primary LAN IP in the SAN so browsing the front door by IP works when
# `industryflow.local` does not resolve (no local DNS/hosts entry) — a common single-host setup.
# Detected from the default route; overridden entirely if CERT_SAN is set explicitly.
_host_ip="$(ip -4 route get 1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}')"
[ -z "$_host_ip" ] && _host_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
_auto_ip_san=""
[ -n "$_host_ip" ] && _auto_ip_san=",IP:$_host_ip"
CERT_SAN="${CERT_SAN:-DNS:industryflow.local,DNS:industryflow,DNS:localhost,IP:127.0.0.1${_auto_ip_san}}"
# GID the served key is made group-readable to, so the non-root frontend nginx (unprivileged
# image, uid/gid 101) can read it when this dir is bind-mounted into the container.
NGINX_GID="${NGINX_GID:-101}"

mkdir -p "$CA_DIR" "$OUT_DIR"

# 1) Root CA — generated once, reused thereafter (clients install ca.crt a single time).
#    Lives in CA_DIR (private); only its public ca.crt is copied to the served dir.
if [ ! -f "$CA_DIR/ca.key" ] || [ ! -f "$CA_DIR/ca.crt" ]; then
    echo "[ca]  generating internal root CA"
    openssl genrsa -out "$CA_DIR/ca.key" 4096
    openssl req -x509 -new -nodes -key "$CA_DIR/ca.key" -sha256 -days "$CA_DAYS" \
        -subj "/O=IndustryFlow/CN=IndustryFlow Internal CA" \
        -addext "basicConstraints=critical,CA:TRUE" \
        -addext "keyUsage=critical,keyCertSign,cRLSign" \
        -out "$CA_DIR/ca.crt"
else
    echo "[ca]  reusing existing root CA (delete $CA_DIR/ca.* to rotate it)"
fi

# 2) Server key + CSR
echo "[srv] issuing server certificate for $PRIMARY_HOST"
openssl genrsa -out "$OUT_DIR/tls.key" 2048
openssl req -new -key "$OUT_DIR/tls.key" \
    -subj "/O=IndustryFlow/CN=$PRIMARY_HOST" -out "$OUT_DIR/tls.csr"

# 3) Sign the leaf with the CA, attaching SANs and a server-auth EKU
cat > "$OUT_DIR/tls.ext" <<EXT
basicConstraints=CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=$CERT_SAN
EXT
openssl x509 -req -in "$OUT_DIR/tls.csr" \
    -CA "$CA_DIR/ca.crt" -CAkey "$CA_DIR/ca.key" -CAcreateserial \
    -days "$CERT_DAYS" -sha256 -extfile "$OUT_DIR/tls.ext" -out "$OUT_DIR/tls.crt"
rm -f "$OUT_DIR/tls.csr" "$OUT_DIR/tls.ext"
# Copy the public CA cert next to the served files for easy client distribution.
cp "$CA_DIR/ca.crt" "$OUT_DIR/ca.crt"
# CA root key stays owner-only. The SERVED leaf key is owner-rw + group-read and grouped to the
# nginx gid, so the non-root frontend nginx can read it via the bind mount (k8s uses fsGroup).
chmod 600 "$CA_DIR/ca.key"
chmod 640 "$OUT_DIR/tls.key"
chgrp "$NGINX_GID" "$OUT_DIR/tls.key" 2>/dev/null || echo "  (note: could not chgrp tls.key to $NGINX_GID — set it so the nginx group can read the key)"

# 4) TimescaleDB server certificate (TLS for DB connections; clients verify it against ca.crt).
#    SAN must include the hostname clients connect to (the in-cluster/compose service name) so
#    sslmode=verify-full passes. Same internal CA, so clients trust both the edge and the DB.
DB_CERT_SAN="${DB_CERT_SAN:-DNS:timescaledb,DNS:localhost,DNS:industryflow.local,IP:127.0.0.1}"
echo "[db]  issuing TimescaleDB server certificate (SAN: $DB_CERT_SAN)"
openssl genrsa -out "$OUT_DIR/db.key" 2048
openssl req -new -key "$OUT_DIR/db.key" -subj "/O=IndustryFlow/CN=timescaledb" -out "$OUT_DIR/db.csr"
cat > "$OUT_DIR/db.ext" <<EXT
basicConstraints=CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=$DB_CERT_SAN
EXT
openssl x509 -req -in "$OUT_DIR/db.csr" \
    -CA "$CA_DIR/ca.crt" -CAkey "$CA_DIR/ca.key" -CAcreateserial \
    -days "$CERT_DAYS" -sha256 -extfile "$OUT_DIR/db.ext" -out "$OUT_DIR/db.crt"
rm -f "$OUT_DIR/db.csr" "$OUT_DIR/db.ext"
# Postgres requires the key be owned by its user (uid 70) or root, and not group/other readable.
# A bind mount can't be re-owned from here without root; the db-cert-init step (compose) /
# initContainer (helm) stages it into a postgres-owned volume with 0600. Keep it 0600 here.
chmod 600 "$OUT_DIR/db.key"

echo ""
echo "CA (private, NOT mounted): $CA_DIR/ca.key, $CA_DIR/ca.crt"
echo "Served dir ($OUT_DIR):"
echo "  ca.crt   -> trusted root: install on client devices AND used as sslrootcert by DB clients"
echo "  tls.crt/tls.key -> frontend nginx server cert  [SAN: $CERT_SAN]"
echo "  db.crt/db.key   -> TimescaleDB server cert     [SAN: $DB_CERT_SAN]"
echo ""
echo "Next: set  TLS_CERT_DIR=$OUT_DIR  in .env, then rebuild/restart the frontend; the DB cert"
echo "is staged into a postgres-owned volume by the db-cert-init service (compose)."
