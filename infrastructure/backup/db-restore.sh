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
log() { echo "[db-restore $(date -u +%H:%M:%S)] $*"; }

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
# NB: no --exit-on-error — the dump re-issues CREATE EXTENSION timescaledb, which collides
# with the one created above; that single error is expected/benign per the TimescaleDB
# restore procedure. Review the pg_restore output and verify row counts afterwards.
"${PSQL[@]}" -d "${TARGET_DB}" -c "CREATE EXTENSION IF NOT EXISTS timescaledb;"
"${PSQL[@]}" -d "${TARGET_DB}" -c "SELECT timescaledb_pre_restore();"
log "running pg_restore"
pg_restore --no-owner --no-privileges \
  -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "${TARGET_DB}" "$DUMP" \
  || log "pg_restore reported errors (the CREATE EXTENSION collision is expected; review above)"
"${PSQL[@]}" -d "${TARGET_DB}" -c "SELECT timescaledb_post_restore();"
log "restore into '${TARGET_DB}' complete — verify row counts before cutover"
