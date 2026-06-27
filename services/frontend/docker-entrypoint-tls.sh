#!/bin/sh
# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

# Generate a self-signed TLS certificate for local development if none is mounted.
# In production, mount a real certificate/key at /etc/nginx/certs (tls.crt / tls.key).
#
# The cert is valid for industryflow.local (the mDNS name advertised on the LAN) plus
# the bare hostname and loopback. Set CERT_EXTRA_SAN to add more names/IPs, e.g.
# CERT_EXTRA_SAN="IP:<host-ip>,DNS:industryflow.example.com".
set -e
CERT_DIR=/etc/nginx/certs
SAN="DNS:industryflow.local,DNS:industryflow,DNS:localhost,IP:127.0.0.1"
if [ -n "$CERT_EXTRA_SAN" ]; then
    SAN="$SAN,$CERT_EXTRA_SAN"
fi
if [ ! -f "$CERT_DIR/tls.crt" ] || [ ! -f "$CERT_DIR/tls.key" ]; then
    mkdir -p "$CERT_DIR"
    echo "[tls] no certificate mounted — generating a self-signed cert (SAN: $SAN)"
    openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
        -keyout "$CERT_DIR/tls.key" -out "$CERT_DIR/tls.crt" \
        -subj "/CN=industryflow.local" \
        -addext "subjectAltName=$SAN" >/dev/null 2>&1
fi
