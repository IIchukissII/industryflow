#!/bin/sh
# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# Platform schema migrations (ADR-0034 dec 4, 7-10).
#
# Applies the numbered scripts of MIGRATIONS_DIR in order and records each in the ledger,
# public.schema_ledger. It is the only applier: a fresh database and a live one run the same
# sequence and end with the same ledger.
#
#   apply    apply every pending script, one run at a time (advisory lock), recording each
#   verify   read-only: exit 0 when the ledger is current, 1 when a script is pending, 2 on a
#            refused script; --wait retries until current (a client waiting to start)
#   status   print the plan without applying anything
#   reapply <definition>...
#            apply the named definitions again although unchanged (e.g. an optional role whose
#            password was set after its script was applied), then everything else pending
#
# Each script declares its kind in a header line, `-- migration-kind: migration|definition`
# (`#` for shell). A migration runs once and is never edited after release: a digest that differs
# from its ledger row is refused. A definition (provisioning and what it calls) is re-applied
# whenever its digest changes. A script with no kind is refused. A database without a ledger
# applies every script once, which dec 2 (every script is idempotent) makes safe.
#
# Connection: libpq's PG* variables, as a network client under ADR-0017 (PGSSLMODE=verify-full).
# `apply` needs the database owner: scripts create databases, roles and extensions. The shell
# scripts read the role passwords from the environment, as they did under the image's init hook,
# whose POSTGRES_USER/POSTGRES_DB they also expect.
set -eu

DIR="${MIGRATIONS_DIR:-/migrations}"
WAIT_SECONDS="${MIGRATE_WAIT_SECONDS:-300}"
LOCK_KEY=873402115
export PGDATABASE="${PGDATABASE:-industryflow}"
export POSTGRES_USER="${POSTGRES_USER:-${PGUSER:-postgres}}"
export POSTGRES_DB="${POSTGRES_DB:-$PGDATABASE}"
export LC_ALL=C

die() { echo "migrate: $*" >&2; exit 2; }

q() { psql -X -qtA -v ON_ERROR_STOP=1 "$@"; }

scripts() {
  for f in "$DIR"/[0-9]*.sql "$DIR"/[0-9]*.sh; do [ -f "$f" ] && echo "$f"; done | sort
}

kind_of() {
  sed -n '1,40s/^\(--\|#\) *migration-kind: *\([a-z]*\) *$/\2/p' "$1" | head -n 1
}

wait_for_db() {
  deadline=$(( $(date +%s) + $1 ))
  until q -c 'SELECT 1' >/dev/null 2>&1; do
    [ "$(date +%s)" -lt "$deadline" ] || { q -c 'SELECT 1' >/dev/null; die "database unreachable"; }
    sleep 2
  done
}

# Writes "$tmp/plan": one line per script, "<action>\t<name>\t<kind>\t<digest>[\t<reason>]",
# action one of apply (no ledger row), reapply (changed definition), current, refused. Any failure
# to read the ledger aborts the command: a database that cannot be read is never "current".
plan() {
  : > "$tmp/ledger"
  exists=$(q -c "SELECT to_regclass('public.schema_ledger') IS NOT NULL") || die "cannot read the ledger"
  if [ "$exists" = t ]; then
    q -F ' ' -c 'SELECT script, digest FROM public.schema_ledger' > "$tmp/ledger" || die "cannot read the ledger"
  fi
  scripts > "$tmp/scripts"
  while read -r f; do
    name=$(basename "$f")
    kind=$(kind_of "$f")
    case "$kind" in
      migration|definition) ;;
      *) printf 'refused\t%s\t-\t-\tno migration-kind header\n' "$name"; continue ;;
    esac
    digest=$(sha256sum "$f" | cut -d' ' -f1)
    recorded=$(awk -v n="$name" '$1 == n { print $2 }' "$tmp/ledger")
    if [ -z "$recorded" ]; then
      printf 'apply\t%s\t%s\t%s\n' "$name" "$kind" "$digest"
    elif [ "$recorded" = "$digest" ]; then
      printf 'current\t%s\t%s\t%s\n' "$name" "$kind" "$digest"
    elif [ "$kind" = definition ]; then
      printf 'reapply\t%s\t%s\t%s\n' "$name" "$kind" "$digest"
    else
      printf 'refused\t%s\t%s\t%s\tmigration changed after it was applied\n' "$name" "$kind" "$digest"
    fi
  done < "$tmp/scripts" > "$tmp/plan"
}

ensure_ledger() {
  q <<'SQL'
SET client_min_messages = warning;
CREATE TABLE IF NOT EXISTS public.schema_ledger (
    script      TEXT PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN ('migration', 'definition')),
    digest      TEXT NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE public.schema_ledger IS
    'ADR-0034 dec 4, 8: every platform schema script applied to this database, written by migrate.sh only';
SQL
}

# Runs inside the lock session (see apply): plans again under the lock, then applies.
apply_locked() {
  plan
  refused=$(awk -F '\t' '$1 == "refused"' "$tmp/plan")
  if [ -n "$refused" ]; then
    printf '%s\n' "$refused" | awk -F '\t' '{ print "migrate: refused " $2 ": " $5 }' >&2
    exit 2
  fi
  n=0
  awk -F '\t' '$1 == "apply" || $1 == "reapply"' "$tmp/plan" > "$tmp/pending"
  while IFS="$(printf '\t')" read -r action name kind digest; do
    echo "migrate: $action $name ($kind)"
    case "$name" in
      *.sh) bash "$DIR/$name" ;;
      *) q -f "$DIR/$name" ;;
    esac
    q -v script="$name" -v kind="$kind" -v digest="$digest" <<'SQL'
INSERT INTO public.schema_ledger (script, kind, digest) VALUES (:'script', :'kind', :'digest')
ON CONFLICT (script) DO UPDATE SET kind = EXCLUDED.kind, digest = EXCLUDED.digest, applied_at = now();
SQL
    n=$((n + 1))
  done < "$tmp/pending"
  echo "migrate: $n script(s) applied; ledger current"
}

cmd_apply() {
  [ -d "$DIR" ] || die "no migrations directory at $DIR"
  wait_for_db "$WAIT_SECONDS"
  ensure_ledger
  # The advisory lock is held by one psql session for the whole run, while each script runs in
  # its own (scripts switch databases, which would drop a lock held on their connection).
  echo 1 > "$tmp/status"
  q <<SQL
SELECT pg_advisory_lock($LOCK_KEY) AS locked \\gset
\! sh "$SELF" _apply_locked; echo \$? > "$tmp/status"
SQL
  return "$(cat "$tmp/status")"
}

cmd_reapply() {
  [ $# -gt 0 ] || die "usage: migrate.sh reapply <definition>..."
  wait_for_db "$WAIT_SECONDS"
  ensure_ledger
  for name in "$@"; do
    f="$DIR/$name"
    [ -f "$f" ] || die "no script $name in $DIR"
    [ "$(kind_of "$f")" = definition ] || die "$name is not a definition; a migration runs once"
  done
  # Forgetting the ledger row makes the next run apply it; the run below holds the lock.
  for name in "$@"; do
    echo "DELETE FROM public.schema_ledger WHERE script = :'script';" | q -v script="$name"
  done
  cmd_apply
}

cmd_verify() {
  [ -d "$DIR" ] || die "no migrations directory at $DIR"
  if [ "${1:-}" = "--wait" ]; then
    deadline=$(( $(date +%s) + WAIT_SECONDS ))
    wait_for_db "$WAIT_SECONDS"
    while plan && behind=$(awk -F '\t' '$1 != "current"' "$tmp/plan") && [ -n "$behind" ]; do
      if printf '%s\n' "$behind" | grep -q '^refused'; then break; fi
      [ "$(date +%s)" -lt "$deadline" ] || break
      echo "migrate: waiting for $(printf '%s\n' "$behind" | wc -l) pending script(s)"
      sleep 5
    done
  else
    wait_for_db 0
    plan
    behind=$(awk -F '\t' '$1 != "current"' "$tmp/plan")
  fi
  [ -z "$behind" ] && { echo "migrate: ledger current"; return 0; }
  printf '%s\n' "$behind" | awk -F '\t' '{ print "migrate: " $1 " " $2 ($5 ? ": " $5 : "") }' >&2
  printf '%s\n' "$behind" | grep -q '^refused' && return 2
  return 1
}

cmd_status() {
  wait_for_db 0
  plan
  awk -F '\t' '{ printf "%-8s %-11s %s\n", $1, $3, $2 }' "$tmp/plan"
}

SELF="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/$(basename -- "$0")"
tmp="${MIGRATE_TMP:-$(mktemp -d)}"
export MIGRATE_TMP="$tmp"

sub="${1:-}"; shift 2>/dev/null || true
case "$sub" in
  apply)  trap 'rm -rf "$tmp"' EXIT; cmd_apply ;;
  _apply_locked) apply_locked ;;
  verify) trap 'rm -rf "$tmp"' EXIT; cmd_verify "$@" ;;
  status) trap 'rm -rf "$tmp"' EXIT; cmd_status ;;
  reapply) trap 'rm -rf "$tmp"' EXIT; cmd_reapply "$@" ;;
  *) echo "usage: migrate.sh {apply|verify [--wait]|status|reapply <definition>...}" >&2; exit 2 ;;
esac
