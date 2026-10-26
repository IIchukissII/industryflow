#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# IndustryFlow logical database backup (tier-1 DR), plus the object-store mirrors (cold layer, models).
#
# Dumps each application database with pg_dump custom format (-Fc, compressed, supports
# selective/parallel restore) plus the cluster globals (roles), and uploads them to an
# S3-compatible object store (MinIO in-cluster by default; point at off-site S3 for real DR).
# Count-based retention prunes old copies per database.
#
# Then it mirrors the cold-layer bucket (ADR-0025) into the same backup target. Once the cold export
# drops a day from the database, the cold bucket holds the only copy of that raw history — a database
# dump no longer contains it — so the backup copies it too. The mirror is incremental: objects missing
# from the mirror (or of a different size) are copied and manifests are re-copied every run (they grow
# as late rows are archived). An object the source no longer holds stays in the mirror for one backup
# retention period after it was first seen missing, then leaves it (ADR-0032 dec 4): an accidental
# delete on the source stays recoverable that long, and a deleted tenant's history does not outlive
# it (ADR-0033 dec 4). When each mirrored object went missing is kept beside the mirror, in
# <COLD_BACKUP_PREFIX>/_absent.tsv.
#
# Model artifacts (the tracking artifact bucket) are mirrored the same way, under their own prefix
# (ADR-0032 dec 8): they exist nowhere else either. The upload staging area is left out; it holds
# unadmitted bytes that expire on their own (ADR-0030 dec 10). Both sources are read with one
# read-only backup principal that no other workload holds (ADR-0032 dec 5).
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
# Object-store mirrors:
#   SOURCE_ENDPOINT_URL             the mirrored buckets' S3 endpoint (default: S3_ENDPOINT_URL)
#   SOURCE_ACCESS_KEY_ID SOURCE_SECRET_ACCESS_KEY
#                                   the read-only backup principal (default: the backup credentials)
#   MIRROR_RETAIN_DAYS              how long an object gone from a source stays mirrored
#                                   (default: RETAIN_PER_DB, the dumps' retention at a daily schedule)
#   COLD_SOURCE_BUCKET              the cold bucket (mirror skipped when unset)
#   COLD_SOURCE_ENDPOINT_URL        its endpoint, when not SOURCE_ENDPOINT_URL
#   COLD_BACKUP_PREFIX              its mirror's key prefix in S3_BUCKET (default: "cold")
#   MODEL_SOURCE_BUCKET             the tracking artifact bucket (mirror skipped when unset)
#   MODEL_BACKUP_PREFIX             its mirror's key prefix in S3_BUCKET (default: "models")
#   MODEL_SOURCE_EXCLUDE_PREFIX     a key prefix left out of the model mirror (default: "_upload-staging/")

set -euo pipefail

log() { echo "[db-backup $(date -u +%H:%M:%S)] $*"; }

# Source and target may be different stores (an in-cluster cold bucket, an off-site backup bucket),
# so each side gets its own endpoint and credentials, passed in the environment of the one command
# (never on argv).
_src_aws() {
  local endpoint="${SOURCE_ENDPOINT_URL:-${S3_ENDPOINT_URL:-}}"
  AWS_ACCESS_KEY_ID="${SOURCE_ACCESS_KEY_ID:-$AWS_ACCESS_KEY_ID}" \
  AWS_SECRET_ACCESS_KEY="${SOURCE_SECRET_ACCESS_KEY:-$AWS_SECRET_ACCESS_KEY}" \
    aws ${endpoint:+--endpoint-url "$endpoint"} "$@"
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

# mirror_bucket <label> <source bucket> <mirror prefix> <exclude prefix> <always re-copied glob>
# Copies what the mirror lacks or holds at a different size; keys matching the glob are re-copied
# every run (rewritten in place at the same size, e.g. a manifest); keys under the exclude prefix are
# not mirrored. Then ages out what the source no longer holds.
mirror_bucket() {
  local label="$1" source="$2" prefix="$3" exclude="$4" recopy="$5"
  : "${S3_BUCKET:?S3_BUCKET required}"
  local work copied=0 kept=0
  work="$(mktemp -d)"

  log "mirroring ${label} bucket s3://${source} -> s3://${S3_BUCKET}/${prefix}/"
  _list_sizes _src_aws "$source" "" | awk -F'\t' -v x="$exclude" 'x == "" || index($1, x) != 1' \
    > "${work}/source"
  _list_sizes _dst_aws "$S3_BUCKET" "${prefix}/" > "${work}/mirror"

  local key size have
  # The listing is read on its own descriptor so no command in the loop can consume it.
  while IFS=$'\t' read -r key size <&3; do
    [ -n "$key" ] || continue
    have="$(awk -F'\t' -v k="${prefix}/${key}" '$1 == k { print $2 }' "${work}/mirror")"
    # shellcheck disable=SC2053  # the glob is a pattern
    if [ "$have" = "$size" ] && { [ -z "$recopy" ] || [[ "$key" != $recopy ]]; }; then
      kept=$((kept + 1))
      continue
    fi
    # Streamed through this job, so source and target may be different stores.
    _src_aws s3 cp "s3://${source}/${key}" - --only-show-errors \
      | _dst_aws s3 cp - "s3://${S3_BUCKET}/${prefix}/${key}" --expected-size "$size" --only-show-errors
    have="$(_dst_aws s3api head-object --bucket "$S3_BUCKET" --key "${prefix}/${key}" \
              --query ContentLength --output text)"
    if [ "$have" != "$size" ]; then
      log "${label} mirror FAILED: ${key} is ${size} bytes at the source but ${have} in the mirror"
      rm -rf "$work"
      return 1
    fi
    copied=$((copied + 1))
  done 3< "${work}/source"
  age_out_mirror "$work" "$prefix" "$label" || { rm -rf "$work"; return 1; }
  rm -rf "$work"
  log "  ${label} mirror: ${copied} object(s) copied, ${kept} already mirrored"
}

mirror_cold_bucket() {
  : "${COLD_SOURCE_BUCKET:?COLD_SOURCE_BUCKET required}"
  local SOURCE_ENDPOINT_URL="${COLD_SOURCE_ENDPOINT_URL:-${SOURCE_ENDPOINT_URL:-}}"
  # Parquet parts are immutable once listed; a manifest is rewritten when a day gains a part.
  mirror_bucket cold "$COLD_SOURCE_BUCKET" "${COLD_BACKUP_PREFIX:-cold}" "" "*/_manifest.json"
}

mirror_model_bucket() {
  : "${MODEL_SOURCE_BUCKET:?MODEL_SOURCE_BUCKET required}"
  # A logged model's files are written once.
  mirror_bucket model "$MODEL_SOURCE_BUCKET" "${MODEL_BACKUP_PREFIX:-models}" \
    "${MODEL_SOURCE_EXCLUDE_PREFIX-_upload-staging/}" ""
}

# Remove from the mirror what the source has not held for MIRROR_RETAIN_DAYS (ADR-0032 dec 4).
# $1 holds the run's listings (source, mirror); $2 is the mirror's prefix, $3 its label. MIRROR_NOW
# (epoch seconds) stands in for the clock in tests.
age_out_mirror() {
  local work="$1" prefix="$2" label="$3" state_key="${2}/_absent.tsv"
  local retain_days="${MIRROR_RETAIN_DAYS:-${RETAIN_PER_DB:-14}}" now expired=0 key
  now="${MIRROR_NOW:-$(date -u +%s)}"
  if ! [ -s "${work}/source" ] && grep -qv "^${state_key}"$'\t' "${work}/mirror" 2>/dev/null; then
    # An empty source beside a populated mirror is far likelier a wrong bucket or credential than a
    # source that lost everything; ageing on it would empty the backup one retention later.
    log "  ${label} mirror: the source lists nothing but the mirror holds objects; ageing skipped"
    return 0
  fi
  _dst_aws s3 cp "s3://${S3_BUCKET}/${state_key}" "${work}/absent" --only-show-errors 2>/dev/null \
    || : > "${work}/absent"
  : > "${work}/expired"; : > "${work}/absent.next"
  awk -F'\t' -v OFS='\t' -v p="${prefix}/" -v state="$state_key" -v now="$now" \
      -v retain=$(( retain_days * 86400 )) -v expired="${work}/expired" -v pending="${work}/absent.next" '
    FILENAME == ARGV[1] { in_source[$1] = 1; next }
    FILENAME == ARGV[2] { since[$1] = $2; next }
    $1 == state || index($1, p) != 1 { next }
    {
      k = substr($1, length(p) + 1)
      if (k in in_source) next
      s = (k in since) ? since[k] : now
      if (now - s >= retain) print $1 > expired
      else print k, s > pending
    }' "${work}/source" "${work}/absent" "${work}/mirror"
  while IFS= read -r key <&4; do
    [ -n "$key" ] || continue
    _dst_aws s3 rm "s3://${S3_BUCKET}/${key}" --only-show-errors
    expired=$((expired + 1))
  done 4< "${work}/expired"
  _dst_aws s3 cp "${work}/absent.next" "s3://${S3_BUCKET}/${state_key}" --only-show-errors
  log "  ${label} mirror: ${expired} object(s) gone from the source for ${retain_days} days removed, $(wc -l < "${work}/absent.next") ageing"
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

  # Cluster-wide roles and role memberships (object grants live in each database dump) — restored
  # before any per-database dump.
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
  if [ -n "${MODEL_SOURCE_BUCKET:-}" ]; then
    mirror_model_bucket
  else
    log "MODEL_SOURCE_BUCKET unset — model-artifact mirror skipped"
  fi
  log "backup run ${STAMP} complete"
}

# Sourcing the script (the tests do) defines the functions without running a backup.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
