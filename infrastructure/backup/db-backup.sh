#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# IndustryFlow logical database backup (tier-1 DR), plus the cold-layer mirror.
#
# Dumps each application database with pg_dump custom format (-Fc, compressed, supports
# selective/parallel restore) plus the cluster globals (roles), and uploads them to an
# S3-compatible object store (MinIO in-cluster by default; point at off-site S3 for real DR).
# Count-based retention prunes old copies per database.
#
# Then it mirrors the cold-layer bucket (ADR-0025) into the same backup target. Once the cold export
# drops a day from the database, the cold bucket holds the only copy of that raw history — a database
# dump no longer contains it — so the backup copies it too. The mirror is incremental and append-only:
# objects missing from the mirror (or of a different size) are copied, manifests are re-copied every
# run (they grow as late rows are archived), and nothing is ever deleted from the mirror, so a delete
# or an overwrite on the source cannot propagate into the backup.
#
# This is the baseline; recovery granularity is the backup interval (RPO = schedule period).
# Continuous WAL archiving for true PITR (pgBackRest) is the documented tier-2 upgrade — see
# docs/operations/backup-and-recovery.md.
#
# Required env:
#   PGHOST PGPORT PGUSER PGPASSWORD   postgres superuser connection (reads all databases)
#   S3_BUCKET                         target bucket (created if absent)
#   AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY   object-store credentials
# Optional env:
#   DATABASES        space-separated db list            (default: "industryflow mlflow")
#   S3_ENDPOINT_URL  S3 endpoint (MinIO)                (default: unset = real AWS S3)
#   AWS_DEFAULT_REGION                                  (default: us-east-1)
#   S3_PREFIX        key prefix within the bucket        (default: "timescaledb")
#   RETAIN_PER_DB    copies to keep per database         (default: 14)
# Cold-layer mirror (skipped when COLD_SOURCE_BUCKET is unset):
#   COLD_SOURCE_BUCKET              the cold bucket to mirror
#   COLD_SOURCE_ENDPOINT_URL        its S3 endpoint     (default: S3_ENDPOINT_URL)
#   COLD_SOURCE_ACCESS_KEY_ID COLD_SOURCE_SECRET_ACCESS_KEY
#                                   a read-only principal for it (default: the backup credentials)
#   COLD_BACKUP_PREFIX              key prefix of the mirror in S3_BUCKET (default: "cold")

set -euo pipefail

log() { echo "[db-backup $(date -u +%H:%M:%S)] $*"; }

# Source and target may be different stores (an in-cluster cold bucket, an off-site backup bucket),
# so each side gets its own endpoint and credentials, passed in the environment of the one command
# (never on argv).
_src_aws() {
  AWS_ACCESS_KEY_ID="${COLD_SOURCE_ACCESS_KEY_ID:-$AWS_ACCESS_KEY_ID}" \
  AWS_SECRET_ACCESS_KEY="${COLD_SOURCE_SECRET_ACCESS_KEY:-$AWS_SECRET_ACCESS_KEY}" \
    aws ${COLD_SOURCE_ENDPOINT_URL:+--endpoint-url "$COLD_SOURCE_ENDPOINT_URL"} "$@"
}

_dst_aws() {
  aws ${S3_ENDPOINT_URL:+--endpoint-url "$S3_ENDPOINT_URL"} "$@"
}

# "<key>\t<size>" for every object under a bucket/prefix (the CLI paginates); nothing if empty.
_list_sizes() {
  local side="$1" bucket="$2" prefix="$3" out
  out="$("$side" s3api list-objects-v2 --bucket "$bucket" ${prefix:+--prefix "$prefix"} \
           --query 'Contents[].[Key,Size]' --output text)"
  [ "$out" = "None" ] || printf '%s\n' "$out" | grep -v '^$' || true
}

mirror_cold_bucket() {
  : "${COLD_SOURCE_BUCKET:?COLD_SOURCE_BUCKET required}"
  : "${S3_BUCKET:?S3_BUCKET required}"
  COLD_SOURCE_ENDPOINT_URL="${COLD_SOURCE_ENDPOINT_URL:-${S3_ENDPOINT_URL:-}}"
  local prefix="${COLD_BACKUP_PREFIX:-cold}"
  local work copied=0 kept=0
  work="$(mktemp -d)"

  log "mirroring cold bucket s3://${COLD_SOURCE_BUCKET} -> s3://${S3_BUCKET}/${prefix}/"
  _list_sizes _src_aws "$COLD_SOURCE_BUCKET" "" > "${work}/source"
  _list_sizes _dst_aws "$S3_BUCKET" "${prefix}/" > "${work}/mirror"

  local key size have
  # The listing is read on its own descriptor so no command in the loop can consume it.
  while IFS=$'\t' read -r key size <&3; do
    [ -n "$key" ] || continue
    have="$(awk -F'\t' -v k="${prefix}/${key}" '$1 == k { print $2 }' "${work}/mirror")"
    # Parquet parts are immutable once listed; a manifest is rewritten when a day gains a part.
    if [ "$have" = "$size" ] && [[ "$key" != */_manifest.json ]]; then
      kept=$((kept + 1))
      continue
    fi
    # Streamed through this job, so source and target may be different stores.
    _src_aws s3 cp "s3://${COLD_SOURCE_BUCKET}/${key}" - --only-show-errors \
      | _dst_aws s3 cp - "s3://${S3_BUCKET}/${prefix}/${key}" --expected-size "$size" --only-show-errors
    have="$(_dst_aws s3api head-object --bucket "$S3_BUCKET" --key "${prefix}/${key}" \
              --query ContentLength --output text)"
    if [ "$have" != "$size" ]; then
      log "cold mirror FAILED: ${key} is ${size} bytes at the source but ${have} in the mirror"
      rm -rf "$work"
      return 1
    fi
    copied=$((copied + 1))
  done 3< "${work}/source"
  rm -rf "$work"
  log "  cold mirror: ${copied} object(s) copied, ${kept} already mirrored"
}

prune() {
  # Keep the newest $RETAIN_PER_DB objects under a prefix; delete the rest. Keys are
  # timestamp-named so lexical sort == chronological.
  local prefix="$1"
  local keys
  keys="$(aws "${AWS_ARGS[@]}" s3api list-objects-v2 --bucket "${S3_BUCKET}" \
            --prefix "${prefix}" --query 'Contents[].Key' --output text 2>/dev/null || true)"
  [ -z "$keys" ] || [ "$keys" = "None" ] && return 0
  local sorted total drop
  sorted="$(printf '%s\n' $keys | sort)"
  total="$(printf '%s\n' "$sorted" | wc -l)"
  drop=$(( total - RETAIN_PER_DB ))
  [ "$drop" -le 0 ] && return 0
  log "  pruning ${drop} old object(s) under ${prefix} (keep ${RETAIN_PER_DB})"
  printf '%s\n' "$sorted" | head -n "$drop" | while read -r k; do
    [ -n "$k" ] && aws "${AWS_ARGS[@]}" s3 rm "s3://${S3_BUCKET}/${k}" --only-show-errors
  done
}

main() {
  : "${PGHOST:?PGHOST required}"
  : "${PGPORT:=5432}"
  : "${PGUSER:?PGUSER required}"
  : "${PGPASSWORD:?PGPASSWORD required}"
  : "${S3_BUCKET:?S3_BUCKET required}"
  DATABASES="${DATABASES:-industryflow mlflow}"
  S3_PREFIX="${S3_PREFIX:-timescaledb}"
  RETAIN_PER_DB="${RETAIN_PER_DB:-14}"
  export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-1}"
  export PGPASSWORD

  # aws-cli reads AWS_* from env; --endpoint-url only when targeting MinIO/non-AWS.
  AWS_ARGS=()
  [ -n "${S3_ENDPOINT_URL:-}" ] && AWS_ARGS+=(--endpoint-url "${S3_ENDPOINT_URL}")

  # A single run is stamped once so all artifacts share a sortable, collision-free key segment.
  # (No Bash builtin clock dependency beyond date; runs are scheduled, not concurrent.)
  STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
  WORKDIR="$(mktemp -d)"
  trap 'rm -rf "$WORKDIR"' EXIT

  log "starting backup run ${STAMP} -> s3://${S3_BUCKET}/${S3_PREFIX} (endpoint=${S3_ENDPOINT_URL:-AWS})"

  # Ensure the bucket exists (idempotent; ignore 'already owned by you').
  if ! aws "${AWS_ARGS[@]}" s3api head-bucket --bucket "${S3_BUCKET}" 2>/dev/null; then
    log "creating bucket ${S3_BUCKET}"
    aws "${AWS_ARGS[@]}" s3 mb "s3://${S3_BUCKET}" || true
  fi

  # Cluster-wide role/grant definitions — restored before any per-database dump.
  log "dumping globals (roles)"
  pg_dumpall --globals-only --no-role-passwords -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" \
    > "${WORKDIR}/globals.sql"
  aws "${AWS_ARGS[@]}" s3 cp "${WORKDIR}/globals.sql" \
    "s3://${S3_BUCKET}/${S3_PREFIX}/globals/${STAMP}.sql" --only-show-errors
  log "  uploaded globals/${STAMP}.sql"

  for db in $DATABASES; do
    log "dumping database '${db}'"
    out="${WORKDIR}/${db}.dump"
    # -Fc custom format, max compression; whole-database dump (TimescaleDB-safe — restore uses
    # timescaledb_pre/post_restore, see the runbook).
    pg_dump -Fc -Z6 -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$db" -f "$out"
    size="$(du -h "$out" | cut -f1)"
    aws "${AWS_ARGS[@]}" s3 cp "$out" \
      "s3://${S3_BUCKET}/${S3_PREFIX}/${db}/${STAMP}.dump" --only-show-errors
    log "  uploaded ${db}/${STAMP}.dump (${size})"
    prune "${S3_PREFIX}/${db}/"
  done

  prune "${S3_PREFIX}/globals/"

  if [ -n "${COLD_SOURCE_BUCKET:-}" ]; then
    mirror_cold_bucket
  else
    log "COLD_SOURCE_BUCKET unset — cold-layer mirror skipped"
  fi
  log "backup run ${STAMP} complete"
}

# Sourcing the script (the tests do) defines the functions without running a backup.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
