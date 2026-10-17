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
# Owners and privileges are restored as dumped: object owners (a SECURITY DEFINER function runs as
# its owner; default privileges belong to the role that creates the objects), table, schema and
# function grants, default privileges, and the database's own owner, grants and settings. So every
# role the dump names must already exist — the init scripts create the service roles, the globals
# backup the runtime ones (tenant readers and logins). A dump naming a missing role is refused before
# the target is touched. PGUSER must be a superuser: only a superuser can restore objects to other
# owners.
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

# Every role a restore script names: object owners, grantees and grantors, and the creating role of
# default privileges. Reads the SQL pg_restore generates, in which each of these statements is one
# line starting at column 0 (function bodies are not), and prints each role once, unquoted.
dump_roles() {
  awk '
    function ident(s,   out, i, c) {
      if (substr(s, 1, 1) != "\"") { match(s, /^[A-Za-z_][A-Za-z0-9_$]*/); return substr(s, 1, RLENGTH) }
      out = ""
      for (i = 2; i <= length(s); i++) {
        c = substr(s, i, 1)
        if (c == "\"") { if (substr(s, i + 1, 1) != "\"") return out; i++ }
        out = out c
      }
      return out
    }
    function after_last(s, sep,   i, j) {
      j = 0
      while ((i = index(substr(s, j + 1), sep)) > 0) j += i
      return j ? substr(s, j + length(sep)) : ""
    }
    function emit(r) { if (r != "" && r != "PUBLIC" && !(r in seen)) { seen[r] = 1; print r } }
    /^ALTER .* OWNER TO .*;$/ { emit(ident(after_last($0, " OWNER TO "))) }
    /^ALTER DEFAULT PRIVILEGES FOR ROLE / { emit(ident(substr($0, 35))) }
    /^(ALTER DEFAULT PRIVILEGES .* )?GRANT .* TO .*;$/ {
      s = $0; sub(/ GRANTED BY .*/, ";", s); emit(ident(after_last(s, " TO ")))
    }
    /^(ALTER DEFAULT PRIVILEGES .* )?REVOKE .* FROM .*;$/ {
      s = $0; sub(/ GRANTED BY .*/, ";", s); sub(/ CASCADE;$/, ";", s); emit(ident(after_last(s, " FROM ")))
    }
    / GRANTED BY .*;$/ { emit(ident(after_last($0, " GRANTED BY "))) }
  ' "$@"
}

# The database-level entries of a dump's table of contents (pg_restore -C -l): the database's
# settings, ACL, comment and security label. pg_restore restores them only with --create, which this
# script cannot use: the extension must exist, in pre-restore mode, before anything is restored.
database_level_entries() {
  awk '$4 == "DATABASE" && $5 == "PROPERTIES" { print; next }
       ($4 == "ACL" || $4 == "COMMENT") && $5 == "-" && $6 == "DATABASE" { print; next }
       $4 == "SECURITY" && $5 == "LABEL" && $6 == "-" && $7 == "DATABASE" { print }' "$1"
}

# The owner of the dumped database (its DATABASE entry in pg_restore -C -l), or nothing.
database_owner() {
  awk '$4 == "DATABASE" && $5 == "-" { print $NF; exit }' "$1"
}

# Points database-level statements at the restore target: "DATABASE <source>" (bare or quoted, as
# pg_dump writes it) becomes "DATABASE <target>" in GRANT/REVOKE/ALTER/COMMENT/SECURITY LABEL lines.
# With --create pg_restore always emits the database's own CREATE DATABASE and \connect, whatever
# -L lists; the target already exists and psql is connected to it, so both are dropped.
retarget_database() {
  local q='"'
  awk -v bare="$1" -v quoted="${q}${1//$q/$q$q}${q}" -v tgt="${q}${2//$q/$q$q}${q}" '
    function swap(line, form,   pat, i, rest, nxt) {
      pat = "DATABASE " form
      i = index(line, pat)
      if (i == 0) return line
      rest = substr(line, i + length(pat)); nxt = substr(rest, 1, 1)
      if (nxt != " " && nxt != ";") return line
      return substr(line, 1, i - 1) "DATABASE " tgt rest
    }
    /^CREATE DATABASE / || /^\\connect/ { next }
    /^(GRANT|REVOKE|ALTER|COMMENT|SECURITY LABEL) / { $0 = swap($0, quoted); $0 = swap($0, bare) }
    { print }
  ' "$3"
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

  # Read the dump before touching the target: the roles it names, the database's owner and
  # database-level entries, and whether it is a TimescaleDB database.
  local toc="${WORKDIR}/toc.list" dblist="${WORKDIR}/database.list"
  local schema_sql="${WORKDIR}/schema.sql" db_sql="${WORKDIR}/database.sql"
  pg_restore -C -l "$DUMP" > "$toc"
  pg_restore -s -f "$schema_sql" "$DUMP"
  database_level_entries "$toc" > "$dblist"
  : > "$db_sql"
  if [ -s "$dblist" ]; then
    pg_restore -C -L "$dblist" -f "$db_sql" "$DUMP"
  fi
  local db_owner timescale=0
  db_owner="$(database_owner "$toc")"
  if grep -qE '^[0-9]+; [0-9]+ [0-9]+ EXTENSION - timescaledb( |$)' "$toc"; then timescale=1; fi

  # Every role the dump names must exist, and only a superuser can restore objects to their owners.
  local roles="${WORKDIR}/roles.txt" check="${WORKDIR}/roles.sql" refused
  { dump_roles "$schema_sql" "$db_sql"; [ -z "$db_owner" ] || echo "$db_owner"; } | sort -u > "$roles"
  cat > "$check" <<SQL
CREATE TEMP TABLE wanted (rolname name);
\\copy wanted FROM '${roles}'
SELECT 'PGUSER is not a superuser: ' || current_user FROM pg_catalog.pg_roles
 WHERE rolname = current_user AND NOT rolsuper;
SELECT 'missing role: ' || w.rolname FROM wanted w
 WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles r WHERE r.rolname = w.rolname) ORDER BY 1;
SQL
  refused="$("${PSQL[@]}" -X -q -t -A -d postgres -f "$check")"
  if [ -n "$refused" ]; then
    log "restore REFUSED, database '${TARGET_DB}' not touched:"
    printf '%s\n' "$refused" | sed 's/^/  /'
    log "restore the globals (…/globals/<stamp>.sql) and run the init scripts first, as a superuser"
    exit 1
  fi

  # Recreate the target database empty, owned as dumped.
  log "(re)creating database '${TARGET_DB}'"
  "${PSQL[@]}" -d postgres -c "DROP DATABASE IF EXISTS \"${TARGET_DB}\" WITH (FORCE);"
  "${PSQL[@]}" -d postgres -c "CREATE DATABASE \"${TARGET_DB}\"${db_owner:+ OWNER \"${db_owner//\"/\"\"}\"};"

  # TimescaleDB-aware restore: install the extension, enter pre-restore mode, pg_restore, then
  # post-restore. Owners and privileges are restored as dumped (see the header).
  # No --exit-on-error: the dump re-issues the extension's statements, which collide with the
  # extension created here — expected per the TimescaleDB restore procedure. Any other error fails
  # the restore (only_extension_collision).
  if [ "$timescale" = 1 ]; then
    "${PSQL[@]}" -d "${TARGET_DB}" -c "CREATE EXTENSION IF NOT EXISTS timescaledb;"
    "${PSQL[@]}" -d "${TARGET_DB}" -c "SELECT timescaledb_pre_restore();"
  fi
  log "running pg_restore"
  RESTORE_LOG="${WORKDIR}/pg_restore.log"
  local rc=0
  pg_restore -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "${TARGET_DB}" "$DUMP" 2>"$RESTORE_LOG" || rc=$?
  cat "$RESTORE_LOG" >&2
  # Leave pre-restore mode whatever happened, so the database is not left with its background jobs
  # stopped.
  if [ "$timescale" = 1 ]; then
    "${PSQL[@]}" -d "${TARGET_DB}" -c "SELECT timescaledb_post_restore();"
  fi
  if [ "$rc" -ne 0 ]; then
    if only_extension_collision "$RESTORE_LOG"; then
      log "pg_restore reported only the expected timescaledb extension collision"
    else
      log "pg_restore FAILED (exit ${rc}) with errors other than the extension collision — see above;" \
          "database '${TARGET_DB}' is incomplete, do not cut over"
      exit 1
    fi
  fi
  # The database's own grants and settings, pointed at the target.
  if [ -s "$db_sql" ]; then
    log "restoring database-level grants and settings"
    retarget_database "$SRC_DB" "$TARGET_DB" "$db_sql" > "${db_sql}.target"
    "${PSQL[@]}" -X -d "${TARGET_DB}" -f "${db_sql}.target"
  fi
  log "restore into '${TARGET_DB}' complete — verify row counts before cutover"
}

# Sourcing the script (the tests do) defines the functions without running a restore.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
