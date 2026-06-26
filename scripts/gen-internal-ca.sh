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
# Example (server, also trusting the LAN IP):
#   CERT_SAN="DNS:industryflow.local,DNS:industryflow,DNS:localhost,IP:127.0.0.1,IP:192.168.178.55" \
#     scripts/gen-internal-ca.sh
set -e

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
CA_DIR="${CA_OUT_DIR:-$ROOT/deploy/ca}"
OUT_DIR="${CERT_OUT_DIR:-$ROOT/deploy/certs}"
CA_DAYS="${CA_DAYS:-3650}"
CERT_DAYS="${CERT_DAYS:-825}"
PRIMARY_HOST="${PRIMARY_HOST:-industryflow.local}"
CERT_SAN="${CERT_SAN:-DNS:industryflow.local,DNS:industryflow,DNS:localhost,IP:127.0.0.1}"

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
chmod 600 "$CA_DIR/ca.key" "$OUT_DIR/tls.key"

echo ""
echo "CA (private, NOT mounted): $CA_DIR/ca.key, $CA_DIR/ca.crt"
echo "Served dir ($OUT_DIR):"
echo "  ca.crt   -> install on client devices as a trusted root (do this once)"
echo "  tls.crt  -> nginx server certificate (leaf, signed by the CA)  [SAN: $CERT_SAN]"
echo "  tls.key  -> nginx server private key"
echo ""
echo "Next: set  TLS_CERT_DIR=$OUT_DIR  in .env, then rebuild/restart the frontend."
