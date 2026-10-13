#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# IndustryFlow logical database restore (tier-1 DR) — companion to db-backup.sh.
#
# Restores one database from an S3/MinIO custom-format dump. TimescaleDB requires the
# restore to be wrapped in timescaledb_pre_restore()/timescaledb_post_restore() so chunk
# DDL and background jobs are handled correctly — this script does that automatically.
#
# DESTRUCTIVE: restoring into an existing database drops and recreates it. Intended to be run
# deliberately by an operator following docs/operations/backup-and-recovery.md, not on a
# schedule. Restore the globals (roles) first if rebuilding a fresh cluster (see runbook).
#
# Required env: PGHOST PGPORT PGUSER PGPASSWORD S3_BUCKET AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY
# Optional env: S3_ENDPOINT_URL AWS_DEFAULT_REGION S3_PREFIX(=timescaledb)
#
# Usage: db-restore.sh <database> [<stamp>|latest] [--target <newdbname>]
#   <database>  source database name as backed up (industryflow | mlflow)
#   <stamp>     backup stamp (e.g. 20260627T020000Z) or "latest" (default)
#   --target    restore into a different db name (safe rehearsal); default = <database>

set -euo pipefail

log() { echo "[db-restore $(date -u +%H:%M:%S)] $*"; }

# pg_restore without --exit-on-error carries on past a failed statement and exits non-zero at the
# end. One failure is expected: the dump re-issues the timescaledb extension's CREATE/COMMENT, which
# collides with the extension created before pre-restore mode. Every other failed statement is a
# restore that did not restore something. pg_restore reports each as
#   pg_restore: error: could not execute query: ERROR:  <message>
#   Command was: <statement>
# This returns success only if every reported error is that collision — and there was at least one,
# so a failure that reports no statement (a lost connection, an unreadable dump) is not excused.
only_extension_collision() {
  awk '
    /^pg_restore: error: / {
      errors++
      pending = $0
      if ($0 ~ /extension "timescaledb" already exists/) { benign++; pending = "" }
      next
    }
    /^Command was: / && pending != "" {
      if ($0 ~ /^Command was: (CREATE|COMMENT ON) EXTENSION (IF NOT EXISTS )?timescaledb[ ;]/) benign++
      pending = ""
      next
    }
    END { exit !(errors > 0 && benign == errors) }
  ' "$1"
}

main() {
  : "${PGHOST:?PGHOST required}"; : "${PGPORT:=5432}"
  : "${PGUSER:?PGUSER required}"; : "${PGPASSWORD:?PGPASSWORD required}"
  : "${S3_BUCKET:?S3_BUCKET required}"
  S3_PREFIX="${S3_PREFIX:-timescaledb}"
  export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-1}"
  export PGPASSWORD

  SRC_DB="${1:?usage: db-restore.sh <database> [<stamp>|latest] [--target <db>]}"
  STAMP="${2:-latest}"
  TARGET_DB="$SRC_DB"
  shift || true; shift || true
  while [ $# -gt 0 ]; do
    case "$1" in
      --target) TARGET_DB="${2:?--target needs a name}"; shift 2 ;;
      *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
  done

  AWS_ARGS=()
  [ -n "${S3_ENDPOINT_URL:-}" ] && AWS_ARGS+=(--endpoint-url "${S3_ENDPOINT_URL}")
  PSQL=(psql -v ON_ERROR_STOP=1 -h "$PGHOST" -p "$PGPORT" -U "$PGUSER")

  # Resolve the dump key.
  if [ "$STAMP" = "latest" ]; then
    KEY="$(aws "${AWS_ARGS[@]}" s3api list-objects-v2 --bucket "$S3_BUCKET" \
            --prefix "${S3_PREFIX}/${SRC_DB}/" --query 'Contents[].Key' --output text \
            | tr '\t' '\n' | sort | tail -n1)"
    [ -n "$KEY" ] && [ "$KEY" != "None" ] || { echo "no backups for ${SRC_DB}" >&2; exit 1; }
  else
    KEY="${S3_PREFIX}/${SRC_DB}/${STAMP}.dump"
  fi
  log "restoring s3://${S3_BUCKET}/${KEY} -> database '${TARGET_DB}'"

  WORKDIR="$(mktemp -d)"; trap 'rm -rf "$WORKDIR"' EXIT
  DUMP="${WORKDIR}/restore.dump"
  aws "${AWS_ARGS[@]}" s3 cp "s3://${S3_BUCKET}/${KEY}" "$DUMP" --only-show-errors

  # Recreate the target database empty.
  log "(re)creating database '${TARGET_DB}'"
  "${PSQL[@]}" -d postgres -c "DROP DATABASE IF EXISTS \"${TARGET_DB}\" WITH (FORCE);"
  "${PSQL[@]}" -d postgres -c "CREATE DATABASE \"${TARGET_DB}\";"

  # TimescaleDB-aware restore: install the extension, enter pre-restore mode, pg_restore, then
  # post-restore. --no-owner/--no-privileges keeps it portable; grants come from globals.sql.
  # No --exit-on-error: the dump re-issues the extension's statements, which collide with the
  # extension created here — expected per the TimescaleDB restore procedure. Any other error fails
  # the restore (only_extension_collision).
  "${PSQL[@]}" -d "${TARGET_DB}" -c "CREATE EXTENSION IF NOT EXISTS timescaledb;"
  "${PSQL[@]}" -d "${TARGET_DB}" -c "SELECT timescaledb_pre_restore();"
  log "running pg_restore"
  RESTORE_LOG="${WORKDIR}/pg_restore.log"
  local rc=0
  pg_restore --no-owner --no-privileges \
    -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "${TARGET_DB}" "$DUMP" 2>"$RESTORE_LOG" || rc=$?
  cat "$RESTORE_LOG" >&2
  # Leave pre-restore mode whatever happened, so the database is not left with its background jobs
  # stopped.
  "${PSQL[@]}" -d "${TARGET_DB}" -c "SELECT timescaledb_post_restore();"
  if [ "$rc" -ne 0 ]; then
    if only_extension_collision "$RESTORE_LOG"; then
      log "pg_restore reported only the expected timescaledb extension collision"
    else
      log "pg_restore FAILED (exit ${rc}) with errors other than the extension collision — see above;" \
          "database '${TARGET_DB}' is incomplete, do not cut over"
      exit 1
    fi
  fi
  log "restore into '${TARGET_DB}' complete — verify row counts before cutover"
}

# Sourcing the script (the tests do) defines the functions without running a restore.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
