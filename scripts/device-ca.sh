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
#                                           a TEST key+CSR — local/test producers only), stored as
#                                           devices/<uuid>/<device_id>/<serial>.{crt,chain.crt,key}
#                                           with current.* naming the newest
#   revoke <company_uuid> <device_id>     revoke every valid cert of that tenant's device
#                                           (renewals included) and refresh the CRL
#   revoke-tenant <company_uuid>          revoke every valid cert of every device of that tenant
#                                           (a deleted tenant, ADR-0033 dec 4) and refresh the CRL
#   crl                                   re-sign the intermediate's CRL (routine; no root key) and
#                                           write the signed issued register beside it
#   root-crl                              re-sign the root's CRL (offline-root ceremony only)
#   check [crl] [chain]                   fail if a published CRL or a CA certificate of the chain
#                                           is close to expiry
#   check-certs [register] [crl] [chain]  fail if a device's newest unrevoked certificate is close
#                                           to expiry (renewal owed)
#   chain                                 print the verify chain path (root+intermediate)
#
# CRL freshness (ADR-0007 dec 8): the edge refuses EVERY device once a CRL it holds expires, so
# each CRL is re-signed well inside its validity. The intermediate's CRL is re-signed routinely
# (`crl`, CRL_DAYS); the root's only at the root ceremony (`root-crl`, ROOT_CRL_DAYS), so the
# root key can stay offline. `check` raises either one, or a CA certificate of the chain, before it
# expires. `check-certs` raises a device certificate whose renewal is owed (ADR-0007 dec 10).
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
CRL_DAYS="${CRL_DAYS:-7}"
ROOT_CRL_DAYS="${ROOT_CRL_DAYS:-365}"
CRL_WARN_DAYS="${CRL_WARN_DAYS:-3}"
ROOT_CRL_WARN_DAYS="${ROOT_CRL_WARN_DAYS:-30}"
CERT_WARN_DAYS="${CERT_WARN_DAYS:-14}"
CA_CERT_WARN_DAYS="${CA_CERT_WARN_DAYS:-90}"
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
default_crl_days  = $CRL_DAYS
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
EOF
}

ROOTCNF="$CA/root.cnf"
write_root_cnf() {
  # Minimal 'ca' config so the offline root can publish its own CRL (covering the
  # intermediate). The terminating proxy needs a CRL for EVERY CA in the chain, so the
  # published list concatenates the root CRL and the intermediate CRL.
  mkdir -p "$CA"
  cat > "$ROOTCNF" <<EOF
[ca]
default_ca = CA_root
[CA_root]
dir               = $ROOT
database          = \$dir/index.txt
crlnumber         = \$dir/crlnumber
certificate       = \$dir/root.crt
private_key       = \$dir/root.key
default_md        = sha256
default_crl_days  = $ROOT_CRL_DAYS
EOF
}

cmd_init() {
  [ -f "$INT/intermediate.crt" ] && die "CA already initialized at $CA (delete it to re-init)"
  mkdir -p "$ROOT" "$INT/newcerts" "$DEV" "$CA/crl"
  : > "$INT/index.txt"; echo 1000 > "$INT/serial"; echo 1000 > "$INT/crlnumber"
  : > "$ROOT/index.txt"; echo 1000 > "$ROOT/crlnumber"

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
  cmd_root_crl
  chmod 600 "$ROOT/root.key" "$INT/intermediate.key"
  echo ""
  echo "Device CA ready:"
  echo "  $CA/ca-chain.crt   -> proxy ssl_client_certificate (verify devices)"
  echo "  $CA/crl/device-ca.crl -> proxy ssl_crl (revocation)"
  echo "Keep $ROOT/root.key offline; the intermediate is the day-to-day signer."
}

identity_args() {
  # Validates <company_uuid> <device_id> and sets $company (lower-case canonical) and $device.
  # The tenant must be a UUID before it is vouched into a SAN or matched (ADR-0003). The
  # device_id lands in the SAN, the CSR subject and a directory name: no '/', no leading dot.
  echo "$1" | grep -qiE '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' \
    || die "company_uuid is not a UUID: $1"
  echo "$2" | grep -qE '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' \
    || die "device_id must match [A-Za-z0-9][A-Za-z0-9._-]{0,127}: $2"
  company=$(echo "$1" | tr 'A-F' 'a-f'); device="$2"
}

cmd_issue() {
  [ -n "$1" ] && [ -n "$2" ] || die "usage: device-ca.sh issue <company_uuid> <device_id> [csr.pem]"
  identity_args "$1" "$2"; csr="$3"
  [ -f "$INT/intermediate.crt" ] || die "CA not initialized — run: device-ca.sh init"
  # One directory per tenant and device, one file set per certificate serial: a renewal is a new
  # serial beside the old one, never an overwrite, and the same device_id under two tenants is two
  # directories. current.* points at the newest, for producers that read a fixed path.
  out="$DEV/$company/$device"; mkdir -p "$out"
  tmp=$(mktemp -d "$out/.issue.XXXXXX")

  if [ -z "$csr" ]; then
    echo "[issue] TEST mode: generating device key on the CA host (NOT for real devices)"
    openssl genrsa -out "$tmp/device.key" 2048
    chmod 600 "$tmp/device.key"
    openssl req -new -key "$tmp/device.key" -subj "/CN=$device" -out "$tmp/device.csr"
    csr="$tmp/device.csr"
  fi

  write_int_cnf "URI:${SAN_PREFIX}/${company}/device/${device}"
  openssl ca -config "$CNF" -batch -notext \
    -in "$csr" -out "$tmp/device.crt" -extensions device_cert
  serial=$(openssl x509 -in "$tmp/device.crt" -noout -serial | sed 's/^serial=//')
  mv "$tmp/device.crt" "$out/$serial.crt"
  [ -f "$tmp/device.key" ] && mv "$tmp/device.key" "$out/$serial.key"
  rm -rf "$tmp"
  # Full chain for the device to present (leaf + intermediate).
  cat "$out/$serial.crt" "$INT/intermediate.crt" > "$out/$serial.chain.crt"
  for ext in crt chain.crt key; do
    rm -f "$out/current.$ext"
    if [ -f "$out/$serial.$ext" ]; then ln -s "$serial.$ext" "$out/current.$ext"; fi
  done
  # A new CRL and register: the register names every certificate the newest CRL's number covers.
  cmd_crl >/dev/null
  echo "issued: $out/$serial.crt   serial=$serial   SAN=URI:${SAN_PREFIX}/${company}/device/${device}"
}

san_names() {
  # The identity this CA vouched for in a certificate's URI SAN: prints "<tenant>/<device>",
  # tenant lower-cased (certificates issued before canonicalization may carry upper case).
  openssl x509 -in "$1" -noout -ext subjectAltName 2>/dev/null | tr ',' '\n' \
    | sed -n "s|^[[:space:]]*URI:${SAN_PREFIX}/\([0-9A-Fa-f-]\{36\}\)/device/\(.*\)$|\1 \2|p" \
    | while read -r t d; do echo "$(echo "$t" | tr 'A-F' 'a-f')/$d"; done
}

cmd_revoke() {
  # Revokes EVERY still-valid certificate the CA issued for this tenant's device: renewals
  # included, and nothing of another tenant's device that shares the device_id. The match is on
  # the SAN of each issued certificate (new_certs_dir keeps them all), not on a file path.
  [ -n "$1" ] && [ -n "$2" ] || die "usage: device-ca.sh revoke <company_uuid> <device_id>"
  identity_args "$1" "$2"
  [ -f "$INT/index.txt" ] || die "CA not initialized — run: device-ca.sh init"
  revoke_where "$company/$device"
  [ "$n" -gt 0 ] || die "no valid certificate for tenant $company device $device"
  cmd_crl
  echo "revoked: $n certificate(s) of $company/$device (CRL refreshed)"
}

revoke_where() {
  # Revokes every still-valid issued certificate whose SAN identity "<tenant>/<device>" matches the
  # grep pattern $1 (fixed string unless $2 is "regex"); sets $n to how many.
  write_int_cnf
  n=0
  for serial in $(awk -F '\t' '$1 == "V" { print $4 }' "$INT/index.txt"); do
    cert="$INT/newcerts/$serial.pem"
    [ -f "$cert" ] || die "issued cert $serial is missing from $INT/newcerts"
    if [ "${2:-}" = regex ]; then match=$(san_names "$cert" | grep -cxE "$1" || true)
    else match=$(san_names "$cert" | grep -cxF "$1" || true); fi
    if [ "$match" -gt 0 ]; then
      openssl ca -config "$CNF" -revoke "$cert"
      echo "revoked serial $serial"
      n=$((n + 1))
    fi
  done
}

cmd_revoke_tenant() {
  # A deleted tenant (ADR-0033 dec 4): revokes every still-valid certificate of every device of the
  # tenant, and refreshes the CRL. Idempotent: a tenant with nothing left to revoke succeeds with 0.
  [ -n "$1" ] || die "usage: device-ca.sh revoke-tenant <company_uuid>"
  identity_args "$1" placeholder
  [ -f "$INT/index.txt" ] || die "CA not initialized — run: device-ca.sh init"
  revoke_where "$company/.+" regex
  cmd_crl
  echo "revoked: $n certificate(s) of tenant $company (CRL refreshed)"
}

publish_bundle() {
  # The verifying proxy needs a CRL for every CA in the chain: root (revoked intermediates) +
  # intermediate (revoked devices). Written via rename so the edge never reads a half-written file.
  cat "$CA/crl/root.crl" "$CA/crl/intermediate.crl" > "$CA/crl/device-ca.crl.tmp"
  mv "$CA/crl/device-ca.crl.tmp" "$CA/crl/device-ca.crl"
  echo "CRL: $CA/crl/device-ca.crl (root + intermediate)"
}

cmd_crl() {
  # Routine refresh: re-signs only the intermediate's CRL, so it needs no root key.
  [ -f "$CA/crl/root.crl" ] || die "no root CRL - run: device-ca.sh root-crl (needs the root key)"
  write_int_cnf
  openssl ca -config "$CNF" -gencrl -out "$CA/crl/intermediate.crl.tmp"
  mv "$CA/crl/intermediate.crl.tmp" "$CA/crl/intermediate.crl"
  write_register
  publish_bundle
}

write_register() {
  # The issued register, beside the CRL it names: every certificate the intermediate ever signed,
  # "<serial> <notAfter> <tenant> <device>" (tab-separated), and the number of that CRL. Public, no
  # key: a verifier holding only the chain and the CRL learns which certificates of a tenant exist,
  # so it can check every unexpired one is revoked (ADR-0033 dec 4). Signed with the issuing key.
  [ -f "$INT/intermediate.key" ] || die "no issuing key at $INT/intermediate.key"
  reg="$CA/crl/issued.tsv"
  crlnum=$(openssl crl -in "$CA/crl/intermediate.crl" -noout -crlnumber | sed 's/^crlNumber=//')
  {
    echo "# IndustryFlow device CA: every certificate the issuing intermediate signed (ADR-0007)"
    printf 'crl_number\t%s\n' "$crlnum"
    awk -F '\t' '{ print $4 "\t" $2 }' "$INT/index.txt" | while IFS="$(printf '\t')" read -r serial not_after; do
      cert="$INT/newcerts/$serial.pem"
      [ -f "$cert" ] || die "issued cert $serial is missing from $INT/newcerts"
      san_names "$cert" | while IFS=/ read -r t d; do printf '%s\t%s\t%s\t%s\n' "$serial" "$not_after" "$t" "$d"; done
    done
  } > "$reg.tmp"
  openssl dgst -sha256 -sign "$INT/intermediate.key" -out "$reg.sig.tmp" "$reg.tmp"
  mv "$reg.sig.tmp" "$reg.sig"
  mv "$reg.tmp" "$reg"
  echo "register: $reg (+ .sig)"
}

cmd_root_crl() {
  # Offline-root ceremony: re-signs the root's CRL (long validity), then the intermediate's.
  [ -f "$ROOT/root.key" ] || die "no root key at $ROOT/root.key - bring the offline root in to re-sign its CRL"
  write_root_cnf
  openssl ca -config "$ROOTCNF" -gencrl -out "$CA/crl/root.crl"
  cmd_crl
}

epoch() {
  # "Oct 18 12:00:00 2026 GMT" -> seconds. GNU date parses it; busybox needs the format.
  date -u -d "$1" +%s 2>/dev/null \
    || date -u -D '%b %d %H:%M:%S %Y' -d "$(echo "${1% GMT}" | tr -s ' ')" +%s
}

cmd_check() {
  # Fails (non-zero) when any CRL in the published bundle is inside its warning window. The
  # root's CRL is told apart by its issuer: the subject of the self-signed cert in the chain.
  bundle="${1:-$CA/crl/device-ca.crl}"; chain="${2:-$CA/ca-chain.crt}"
  [ -f "$bundle" ] || die "no CRL bundle at $bundle"
  [ -f "$chain" ] || die "no CA chain at $chain"
  tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
  awk -v d="$tmp" '/BEGIN X509 CRL/{n++} n{print > (d "/crl" n ".pem")}' "$bundle"
  awk -v d="$tmp" '/BEGIN CERTIFICATE/{n++} n{print > (d "/cert" n ".pem")}' "$chain"
  root_subject=""
  for c in "$tmp"/cert*.pem; do
    sub=$(openssl x509 -in "$c" -noout -subject -nameopt RFC2253 | sed 's/^subject=//')
    iss=$(openssl x509 -in "$c" -noout -issuer -nameopt RFC2253 | sed 's/^issuer=//')
    [ "$sub" = "$iss" ] && root_subject="$sub"
  done
  now=$(date -u +%s); rc=0
  # A CA certificate that expires refuses every device as surely as an expired CRL does (dec 10).
  for c in "$tmp"/cert*.pem; do
    sub=$(openssl x509 -in "$c" -noout -subject -nameopt RFC2253 | sed 's/^subject=//')
    end=$(openssl x509 -in "$c" -noout -enddate | sed 's/^notAfter=//')
    left=$(( ($(epoch "$end") - now) / 86400 ))
    if [ "$left" -lt "$CA_CERT_WARN_DAYS" ]; then
      echo "STALE: CA certificate $sub expires in ${left}d ($end), warn at ${CA_CERT_WARN_DAYS}d" >&2; rc=1
    else
      echo "ok: CA certificate $sub expires in ${left}d ($end)"
    fi
  done
  for f in "$tmp"/crl*.pem; do
    [ -f "$f" ] || die "no CRL found in $bundle"
    iss=$(openssl crl -in "$f" -noout -issuer -nameopt RFC2253 | sed 's/^issuer=//')
    next=$(openssl crl -in "$f" -noout -nextupdate | sed 's/^nextUpdate=//')
    left=$(( ($(epoch "$next") - now) / 86400 ))
    warn=$CRL_WARN_DAYS; [ "$iss" = "$root_subject" ] && warn=$ROOT_CRL_WARN_DAYS
    if [ "$left" -lt "$warn" ]; then
      echo "STALE: CRL of $iss expires in ${left}d ($next), warn at ${warn}d" >&2; rc=1
    else
      echo "ok: CRL of $iss expires in ${left}d ($next)"
    fi
  done
  return $rc
}

cmd_check_certs() {
  # Fails (non-zero) when a device's newest unrevoked certificate is inside its warning window
  # (ADR-0007 dec 10): renewal is owed before then. Reads only public files: the register (its
  # signature verified against the issuing intermediate), the CRL bundle and the chain. A device
  # whose certificates have all expired is reported but does not fail: the edge already refuses
  # it, and revoking it is what decommissions it.
  reg="${1:-$CA/crl/issued.tsv}"; bundle="${2:-$CA/crl/device-ca.crl}"; chain="${3:-$CA/ca-chain.crt}"
  for f in "$reg" "$reg.sig" "$bundle" "$chain"; do [ -f "$f" ] || die "missing $f"; done
  tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
  awk -v d="$tmp" '/BEGIN X509 CRL/{n++} n{print > (d "/crl" n ".pem")}' "$bundle"
  awk -v d="$tmp" '/BEGIN CERTIFICATE/{n++} n{print > (d "/cert" n ".pem")}' "$chain"
  int=""
  for c in "$tmp"/cert*.pem; do
    sub=$(openssl x509 -in "$c" -noout -subject -nameopt RFC2253 | sed 's/^subject=//')
    iss=$(openssl x509 -in "$c" -noout -issuer -nameopt RFC2253 | sed 's/^issuer=//')
    if [ "$sub" != "$iss" ]; then
      [ -z "$int" ] || die "the chain holds more than one issuing intermediate"
      int="$c"; int_subject="$sub"
    fi
  done
  [ -n "$int" ] || die "no issuing intermediate in $chain"
  openssl x509 -in "$int" -noout -pubkey > "$tmp/int.pub"
  openssl dgst -sha256 -verify "$tmp/int.pub" -signature "$reg.sig" "$reg" >/dev/null 2>&1 \
    || die "the issued register's signature does not verify against the issuing intermediate"
  crl=""
  for f in "$tmp"/crl*.pem; do
    [ -f "$f" ] || die "no CRL found in $bundle"
    iss=$(openssl crl -in "$f" -noout -issuer -nameopt RFC2253 | sed 's/^issuer=//')
    [ "$iss" = "$int_subject" ] && crl="$f"
  done
  [ -n "$crl" ] || die "no CRL of the issuing intermediate in $bundle"
  openssl crl -in "$crl" -noout -CAfile "$int" >/dev/null 2>&1 \
    || die "the intermediate's CRL does not verify against the issuing intermediate"
  crlnum=$(openssl crl -in "$crl" -noout -crlnumber | sed 's/^crlNumber=//')
  named=$(awk -F '\t' '$1 == "crl_number" { print $2 }' "$reg")
  [ "$named" = "$crlnum" ] \
    || die "the issued register names CRL $named, the published CRL is $crlnum: republish both together"
  openssl crl -in "$crl" -noout -text | awk '/Serial Number:/ { print $3 }' > "$tmp/revoked"
  # Newest unrevoked notAfter per device, as YYYYMMDDHHMMSS (index.txt keeps UTCTime or
  # GeneralizedTime; RFC 5280 puts UTCTime years 50-99 in the 1900s).
  awk -F '\t' '
    function norm(s) { s = toupper(s); sub(/^0+/, "", s); return s }
    FILENAME == ARGV[1] { revoked[norm($1)] = 1; next }
    /^#/ || $1 == "crl_number" || NF != 4 { next }
    norm($1) in revoked { next }
    {
      t = $2; sub(/Z$/, "", t)
      if (length(t) == 12) t = (substr(t, 1, 2) < 50 ? "20" : "19") t
      k = $3 "/" $4
      if (!(k in latest) || t > latest[k]) latest[k] = t
    }
    END { for (k in latest) print latest[k] "\t" k }
  ' "$tmp/revoked" "$reg" | sort > "$tmp/latest"
  warn=$CERT_WARN_DAYS; now=$(date -u +%s); rc=0; ok=0
  while IFS="$(printf '\t')" read -r t dev; do
    t=$(echo "$t" | sed 's/^\(....\)\(..\)\(..\)\(..\)\(..\)\(..\)$/\1-\2-\3 \4:\5:\6/')
    secs=$(( $(date -u -d "$t" +%s) - now )); left=$(( secs / 86400 ))
    if [ "$secs" -le 0 ]; then
      echo "expired: device $dev ($t UTC); revoke it if it is decommissioned"
    elif [ "$left" -lt "$warn" ]; then
      echo "EXPIRING: device $dev certificate expires in ${left}d ($t UTC), warn at ${warn}d; renew it" >&2; rc=1
    else
      ok=$((ok + 1))
    fi
  done < "$tmp/latest"
  echo "ok: $ok device(s) valid beyond ${warn}d"
  return $rc
}

cmd_chain() { echo "$CA/ca-chain.crt"; }

sub="${1:-}"; shift 2>/dev/null || true
case "$sub" in
  init)   cmd_init ;;
  issue)  cmd_issue "$@" ;;
  revoke) cmd_revoke "$@" ;;
  revoke-tenant) cmd_revoke_tenant "$@" ;;
  crl)    cmd_crl ;;
  root-crl) cmd_root_crl ;;
  check)  cmd_check "$@" ;;
  check-certs) cmd_check_certs "$@" ;;
  chain)  cmd_chain ;;
  *) echo "usage: device-ca.sh {init|issue <uuid> <device_id> [csr]|revoke <uuid> <device_id>|revoke-tenant <uuid>|crl|root-crl|check [crl] [chain]|check-certs [register] [crl] [chain]|chain}" >&2; exit 1 ;;
esac
