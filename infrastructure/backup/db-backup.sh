#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# IndustryFlow logical database backup (tier-1 DR).
#
# Dumps each application database with pg_dump custom format (-Fc, compressed, supports
# selective/parallel restore) plus the cluster globals (roles), and uploads them to an
# S3-compatible object store (MinIO in-cluster by default; point at off-site S3 for real DR).
# Count-based retention prunes old copies per database.
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

set -euo pipefail

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

log() { echo "[db-backup $(date -u +%H:%M:%S)] $*"; }

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
log "backup run ${STAMP} complete"
