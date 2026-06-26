#!/bin/sh
# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# IndustryFlow device certificate authority (ADR-0007).
#
# Two-tier hierarchy: an offline root CA signs one online issuing intermediate; the
# intermediate signs device client certificates. Device identity (tenant + device) is
# carried in a URI SAN — industryflow:tenant/<company_uuid>/device/<device_id> — read at
# verification time (ADR-0002 dec 6 / ADR-0007 dec 3). The CA signs CSRs and never holds a
# device private key (ADR-0007 dec 2); a device's key stays on the device / secure element.
#
# This tool is the operator side of enrollment: it authorizes a CSR for a specific
# company_id at issuance, so the tenant in the SAN is one the CA vouched for (ADR-0007 d4).
#
# Subcommands
#   init                                  create root + issuing intermediate + empty CRL
#   issue <company_uuid> <device_id> [csr]  sign a device cert (CSR if given; else generate
#                                           a TEST key+CSR — local/test producers only)
#   revoke <device_id>                    revoke a device cert and refresh the CRL
#   crl                                   regenerate the CRL
#   chain                                 print the verify chain path (root+intermediate)
#
# Output (git-ignored — holds private keys): deploy/device-ca/
set -e

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
CA="${DEVICE_CA_DIR:-$ROOT_DIR/deploy/device-ca}"
ROOT="$CA/root"
INT="$CA/intermediate"
DEV="$CA/devices"
ROOT_DAYS="${ROOT_DAYS:-3650}"
INT_DAYS="${INT_DAYS:-1825}"
DEVICE_DAYS="${DEVICE_DAYS:-90}"
SAN_PREFIX="industryflow:tenant"

die() { echo "error: $*" >&2; exit 1; }

CNF="$CA/ca.cnf"
write_int_cnf() {
  # openssl 'ca' config for the issuing intermediate (signing + CRL). Written to a file
  # because POSIX sh has no process substitution; the SAN is written literally ($1) so
  # CRL/revoke (which pass no SAN) don't trip an unresolved ${ENV::SAN}.
  san="${1:-URI:industryflow:placeholder}"
  mkdir -p "$CA"
  cat > "$CNF" <<EOF
[ca]
default_ca = CA_default
[CA_default]
dir               = $INT
database          = \$dir/index.txt
serial            = \$dir/serial
crlnumber         = \$dir/crlnumber
new_certs_dir     = \$dir/newcerts
certificate       = \$dir/intermediate.crt
private_key       = \$dir/intermediate.key
default_md        = sha256
default_days      = $DEVICE_DAYS
default_crl_days  = 30
policy            = policy_any
x509_extensions   = device_cert
unique_subject    = no
copy_extensions   = none
[policy_any]
commonName        = optional
[device_cert]
basicConstraints       = critical, CA:FALSE
keyUsage               = critical, digitalSignature, keyEncipherment
extendedKeyUsage       = clientAuth
subjectKeyIdentifier   = hash
authorityKeyIdentifier = keyid,issuer
subjectAltName         = $san
crlDistributionPoints  = URI:http://industryflow.local/device-ca.crl
EOF
}

cmd_init() {
  [ -f "$INT/intermediate.crt" ] && die "CA already initialized at $CA (delete it to re-init)"
  mkdir -p "$ROOT" "$INT/newcerts" "$DEV" "$CA/crl"
  : > "$INT/index.txt"; echo 1000 > "$INT/serial"; echo 1000 > "$INT/crlnumber"

  echo "[root] generating offline root CA ($ROOT_DAYS d)"
  openssl genrsa -out "$ROOT/root.key" 4096
  openssl req -x509 -new -nodes -key "$ROOT/root.key" -sha256 -days "$ROOT_DAYS" \
    -subj "/O=IndustryFlow/CN=IndustryFlow Device Root CA" \
    -addext "basicConstraints=critical,CA:TRUE" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -out "$ROOT/root.crt"

  echo "[int ] generating online issuing intermediate ($INT_DAYS d)"
  openssl genrsa -out "$INT/intermediate.key" 4096
  openssl req -new -key "$INT/intermediate.key" \
    -subj "/O=IndustryFlow/CN=IndustryFlow Device Issuing CA" -out "$INT/intermediate.csr"
  printf 'basicConstraints=critical,CA:TRUE,pathlen:0\nkeyUsage=critical,keyCertSign,cRLSign\n' > "$INT/int-ext.cnf"
  openssl x509 -req -in "$INT/intermediate.csr" \
    -CA "$ROOT/root.crt" -CAkey "$ROOT/root.key" -CAcreateserial \
    -days "$INT_DAYS" -sha256 -extfile "$INT/int-ext.cnf" \
    -out "$INT/intermediate.crt"
  rm -f "$INT/intermediate.csr" "$INT/int-ext.cnf"

  # Verify chain the proxy presents/trusts (root first so it can be the trust anchor too).
  cat "$INT/intermediate.crt" "$ROOT/root.crt" > "$CA/ca-chain.crt"
  cmd_crl
  chmod 600 "$ROOT/root.key" "$INT/intermediate.key"
  echo ""
  echo "Device CA ready:"
  echo "  $CA/ca-chain.crt   -> proxy ssl_client_certificate (verify devices)"
  echo "  $CA/crl/device-ca.crl -> proxy ssl_crl (revocation)"
  echo "Keep $ROOT/root.key offline; the intermediate is the day-to-day signer."
}

cmd_issue() {
  company="$1"; device="$2"; csr="$3"
  [ -n "$company" ] && [ -n "$device" ] || die "usage: device-ca.sh issue <company_uuid> <device_id> [csr.pem]"
  # Validate the tenant is a well-formed UUID before it is vouched into the SAN (ADR-0003).
  echo "$company" | grep -qiE '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' \
    || die "company_uuid is not a UUID: $company"
  [ -f "$INT/intermediate.crt" ] || die "CA not initialized — run: device-ca.sh init"
  out="$DEV/$device"; mkdir -p "$out"

  if [ -z "$csr" ]; then
    echo "[issue] TEST mode: generating device key on the CA host (NOT for real devices)"
    openssl genrsa -out "$out/$device.key" 2048
    openssl req -new -key "$out/$device.key" -subj "/CN=$device" -out "$out/$device.csr"
    csr="$out/$device.csr"; chmod 600 "$out/$device.key"
  fi

  write_int_cnf "URI:${SAN_PREFIX}/${company}/device/${device}"
  openssl ca -config "$CNF" -batch -notext \
    -in "$csr" -out "$out/$device.crt" -extensions device_cert
  rm -f "$out/$device.csr"
  # Full chain for the device to present (leaf + intermediate).
  cat "$out/$device.crt" "$INT/intermediate.crt" > "$out/$device.chain.crt"
  echo "issued: $out/$device.crt   SAN=URI:${SAN_PREFIX}/${company}/device/${device}"
}

cmd_revoke() {
  device="$1"; [ -n "$device" ] || die "usage: device-ca.sh revoke <device_id>"
  [ -f "$DEV/$device/$device.crt" ] || die "no issued cert for device: $device"
  write_int_cnf
  openssl ca -config "$CNF" -revoke "$DEV/$device/$device.crt"
  cmd_crl
  echo "revoked: $device (CRL refreshed)"
}

cmd_crl() {
  write_int_cnf
  openssl ca -config "$CNF" -gencrl -out "$CA/crl/device-ca.crl"
  echo "CRL: $CA/crl/device-ca.crl"
}

cmd_chain() { echo "$CA/ca-chain.crt"; }

sub="${1:-}"; shift 2>/dev/null || true
case "$sub" in
  init)   cmd_init ;;
  issue)  cmd_issue "$@" ;;
  revoke) cmd_revoke "$@" ;;
  crl)    cmd_crl ;;
  chain)  cmd_chain ;;
  *) echo "usage: device-ca.sh {init|issue <uuid> <device_id> [csr]|revoke <device_id>|crl|chain}" >&2; exit 1 ;;
esac
