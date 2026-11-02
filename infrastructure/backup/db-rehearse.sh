#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# Restore rehearsal (ADR-0032 dec 6): proves the latest backup restores, on a schedule.
#
# For each database in DATABASES, the latest dump is restored by db-restore.sh into a scratch database
# (<database><REHEARSAL_SUFFIX>, policies held) and every table's row count is compared with the counts
# db-backup.sh took in the dump's own snapshot: a table missing, added or with another count fails the
# rehearsal. The scratch database is then dropped. For each object mirror (cold layer, model
# artifacts), a sample of objects is read back from the mirror and compared byte for byte with the
# source.
#
# The restore runs on PGHOST: by default the production server, which then briefly holds a second copy
# of each database. Point PGHOST at a scratch server that holds the globals to keep that load off it.
#
# Env as db-backup.sh (PG*, S3_*, AWS_*, DATABASES, SOURCE_*, COLD_*, MODEL_*), plus:
#   REHEARSAL_SUFFIX         scratch database suffix                  (default: _rehearsal)
#   REHEARSAL_OBJECT_SAMPLE  objects compared per mirror              (default: 20)
#   METRICS_TEXTFILE_DIR     node-exporter textfile directory         (unset: no metric)

set -euo pipefail

# shellcheck source=db-backup.sh
source "$(dirname "${BASH_SOURCE[0]}")/db-backup.sh"

log() { echo "[db-rehearse $(date -u +%H:%M:%S)] $*"; }

FAILED=0
RESTORE_SECONDS=()

# The newest dump's stamp of <database>, or nothing.
latest_stamp() {
  aws "${AWS_ARGS[@]}" s3api list-objects-v2 --bucket "$S3_BUCKET" --prefix "${S3_PREFIX}/${1}/" \
      --query 'Contents[].Key' --output text \
    | tr '\t' '\n' | { grep '\.dump$' || true; } | sort | tail -n 1 | sed 's|.*/||; s|\.dump$||'
}

rehearse_database() {
  local db="$1" target="${1}${REHEARSAL_SUFFIX}" stamp started took
  stamp="$(latest_stamp "$db")"
  if [ -z "$stamp" ]; then
    log "${db}: FAILED, no backup to rehearse"; FAILED=1; return
  fi
  if ! aws "${AWS_ARGS[@]}" s3 cp "s3://${S3_BUCKET}/${S3_PREFIX}/counts/${db}/${stamp}.tsv" \
         "${WORKDIR}/${db}.dumped" --only-show-errors 2>/dev/null; then
    log "${db}: FAILED, backup ${stamp} has no row counts (taken before they were recorded); the next backup will"
    FAILED=1; return
  fi

  log "${db}: restoring backup ${stamp} into '${target}'"
  started="$(date -u +%s)"
  if ! db-restore.sh "$db" "$stamp" --target "$target" --hold-jobs > "${WORKDIR}/${db}.restore.log" 2>&1; then
    log "${db}: FAILED, the restore did not complete:"; tail -n 30 "${WORKDIR}/${db}.restore.log"
    drop_scratch "$target"; FAILED=1; return
  fi
  took=$(( $(date -u +%s) - started ))

  # On stdin: -c cannot carry the \gexec that runs the generated counts.
  if ! printf '%s\n' "$COUNT_SQL" | psql -X -q -t -A -F "$(printf '\t')" -v ON_ERROR_STOP=1 \
         -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$target" -f - \
         > "${WORKDIR}/${db}.restored" 2> "${WORKDIR}/${db}.count.err"; then
    log "${db}: FAILED, counting the restored rows:"; cat "${WORKDIR}/${db}.count.err"
    drop_scratch "$target"; FAILED=1; return
  fi
  drop_scratch "$target"

  if ! diff "${WORKDIR}/${db}.dumped" "${WORKDIR}/${db}.restored" > "${WORKDIR}/${db}.diff"; then
    log "${db}: FAILED, the restored rows differ from the backup's (< dumped, > restored):"
    head -n 40 "${WORKDIR}/${db}.diff"
    FAILED=1; return
  fi
  log "${db}: backup ${stamp} restored in ${took}s, $(wc -l < "${WORKDIR}/${db}.restored") tables match their dumped row counts"
  RESTORE_SECONDS+=("industryflow_restore_rehearsal_duration_seconds{database=\"${db}\"} ${took}")
}

drop_scratch() {
  psql -X -q -v ON_ERROR_STOP=1 -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d postgres \
    -c "DROP DATABASE IF EXISTS \"${1}\" WITH (FORCE);" \
    || { log "could not drop the scratch database '${1}'"; FAILED=1; }
}

# rehearse_mirror <label> <source bucket> <mirror prefix> <exclude prefix> <skipped glob>
# An object both sides hold at different sizes fails: the backup re-copies such an object every run, so
# one still differing after it is a mirror that does not hold the source. Then a random sample of the
# objects both hold at the same size is read from each side and compared byte for byte. Keys matching
# the glob are rewritten in place at the source (a manifest) and may legitimately be newer there, so
# they are neither compared nor sampled.
rehearse_mirror() {
  local label="$1" source="$2" prefix="$3" exclude="$4" skip="$5" key size n=0 a b
  _list_sizes _src_aws "$source" "" \
    | awk -F'\t' -v x="$exclude" 'x == "" || index($1, x) != 1' > "${WORKDIR}/${label}.source"
  _list_sizes _dst_aws "$S3_BUCKET" "${prefix}/" \
    | awk -F'\t' -v p="${prefix}/" '{ print substr($1, length(p) + 1) "\t" $2 }' > "${WORKDIR}/${label}.mirror"
  awk -F'\t' 'FILENAME == ARGV[1] { m[$1] = $2; next } ($1 in m) && m[$1] != $2 { print $1 }' \
      "${WORKDIR}/${label}.mirror" "${WORKDIR}/${label}.source" \
    | while IFS= read -r key; do
        # shellcheck disable=SC2053  # the glob is a pattern
        [ -n "$skip" ] && [[ "$key" == $skip ]] && continue
        printf '%s\n' "$key"
      done > "${WORKDIR}/${label}.sizes"
  if [ -s "${WORKDIR}/${label}.sizes" ]; then
    log "${label} mirror: FAILED, $(wc -l < "${WORKDIR}/${label}.sizes") object(s) differ in size from the source, e.g.:"
    head -n 10 "${WORKDIR}/${label}.sizes"
    FAILED=1
  fi
  awk -F'\t' 'FILENAME == ARGV[1] { m[$1] = $2; next } ($1 in m) && m[$1] == $2' \
      "${WORKDIR}/${label}.mirror" "${WORKDIR}/${label}.source" \
    | while IFS=$'\t' read -r key size; do
        # shellcheck disable=SC2053  # the glob is a pattern
        [ -n "$skip" ] && [[ "$key" == $skip ]] && continue
        printf '%s\t%s\n' "$key" "$size"
      done \
    | shuf -n "$SAMPLE" > "${WORKDIR}/${label}.sample" || true
  if ! [ -s "${WORKDIR}/${label}.source" ]; then
    log "${label} mirror: the source holds no objects; nothing to compare"; return
  fi
  if ! [ -s "${WORKDIR}/${label}.sample" ]; then
    log "${label} mirror: FAILED, the source holds objects but the mirror holds none of them"
    FAILED=1; return
  fi
  while IFS=$'\t' read -r key size <&6; do
    a="$(_src_aws s3 cp "s3://${source}/${key}" - --only-show-errors | sha256sum | cut -d' ' -f1)"
    b="$(_dst_aws s3 cp "s3://${S3_BUCKET}/${prefix}/${key}" - --only-show-errors | sha256sum | cut -d' ' -f1)"
    if [ "$a" != "$b" ]; then
      log "${label} mirror: FAILED, ${key} differs between the source and the mirror"
      FAILED=1
    fi
    n=$((n + 1))
  done 6< "${WORKDIR}/${label}.sample"
  log "${label} mirror: ${n} sampled object(s) read back and compared"
}

rehearse_main() {
  : "${PGHOST:?PGHOST required}"; : "${PGPORT:=5432}"
  : "${PGUSER:?PGUSER required}"; : "${PGPASSWORD:?PGPASSWORD required}"
  : "${S3_BUCKET:?S3_BUCKET required}"
  DATABASES="${DATABASES:-industryflow mlflow}"
  S3_PREFIX="${S3_PREFIX:-timescaledb}"
  REHEARSAL_SUFFIX="${REHEARSAL_SUFFIX-_rehearsal}"
  SAMPLE="${REHEARSAL_OBJECT_SAMPLE:-20}"
  export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-1}" PGPASSWORD
  AWS_ARGS=()
  [ -n "${S3_ENDPOINT_URL:-}" ] && AWS_ARGS+=(--endpoint-url "${S3_ENDPOINT_URL}")
  # The scratch name must never be a database the platform uses.
  if [ -z "$REHEARSAL_SUFFIX" ]; then
    log "REHEARSAL_SUFFIX is empty: the rehearsal would restore over the databases themselves"; exit 2
  fi
  WORKDIR="$(mktemp -d)"
  trap 'rm -rf "$WORKDIR"' EXIT

  local db
  for db in $DATABASES; do rehearse_database "$db"; done
  if [ -n "${COLD_SOURCE_BUCKET:-}" ]; then
    SOURCE_ENDPOINT_URL="${COLD_SOURCE_ENDPOINT_URL:-${SOURCE_ENDPOINT_URL:-}}" \
      rehearse_mirror cold "$COLD_SOURCE_BUCKET" "${COLD_BACKUP_PREFIX:-cold}" "" "*/_manifest.json"
  fi
  if [ -n "${MODEL_SOURCE_BUCKET:-}" ]; then
    rehearse_mirror model "$MODEL_SOURCE_BUCKET" "${MODEL_BACKUP_PREFIX:-models}" \
      "${MODEL_SOURCE_EXCLUDE_PREFIX-_upload-staging/}" ""
  fi

  if [ "$FAILED" -ne 0 ]; then
    log "restore rehearsal FAILED, see above"; exit 1
  fi
  write_metric db-rehearse \
    "# HELP industryflow_restore_rehearsal_last_success_timestamp_seconds When a restore rehearsal last passed." \
    "# TYPE industryflow_restore_rehearsal_last_success_timestamp_seconds gauge" \
    "industryflow_restore_rehearsal_last_success_timestamp_seconds{backup=\"databases\"} $(date -u +%s)" \
    "# HELP industryflow_restore_rehearsal_duration_seconds How long the last rehearsal's restore of a database took." \
    "# TYPE industryflow_restore_rehearsal_duration_seconds gauge" \
    "${RESTORE_SECONDS[@]}"
  log "restore rehearsal passed"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  rehearse_main "$@"
fi
