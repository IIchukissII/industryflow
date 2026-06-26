#!/bin/sh
# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

# Generate a self-signed TLS certificate for local development if none is mounted.
# In production, mount a real certificate/key at /etc/nginx/certs (tls.crt / tls.key).
set -e
CERT_DIR=/etc/nginx/certs
if [ ! -f "$CERT_DIR/tls.crt" ] || [ ! -f "$CERT_DIR/tls.key" ]; then
    mkdir -p "$CERT_DIR"
    echo "[tls] no certificate mounted — generating a self-signed cert (local development)"
    openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
        -keyout "$CERT_DIR/tls.key" -out "$CERT_DIR/tls.crt" \
        -subj "/CN=industryflow.local" \
        -addext "subjectAltName=DNS:industryflow,DNS:localhost,IP:127.0.0.1" >/dev/null 2>&1
fi
