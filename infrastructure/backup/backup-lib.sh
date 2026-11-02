#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# Shared by the backup and rehearsal scripts (sourced, never run).

# One query per table: "<schema>.<table>\t<rows>", ordered by name. The statements are generated and
# run by psql's \gexec. TimescaleDB's own schemas are left out: a hypertable's rows are counted through
# the hypertable, not its chunks, so the count does not depend on how the rows are chunked.
# shellcheck disable=SC2034  # used by the scripts that source this file
COUNT_SQL="SELECT format('SELECT %L, count(*) FROM %I.%I', n.nspname || '.' || c.relname, n.nspname, c.relname)
  FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relkind IN ('r', 'p') AND NOT c.relispartition
   AND n.nspname NOT IN ('pg_catalog', 'information_schema')
   AND n.nspname !~ '^(pg_toast|pg_temp|_timescaledb|timescaledb)'
 ORDER BY 1
\\gexec"

# write_metric <file stem> <metric lines...>
# Writes a node-exporter textfile (<METRICS_TEXTFILE_DIR>/<stem>.prom) atomically: a scrape sees the
# old file or the new one, never half of one. Does nothing when METRICS_TEXTFILE_DIR is unset (on
# Kubernetes the CronJob's own status is the signal). A write that fails is reported, not fatal: the
# work it reports is done, and the stale-success alert raises the missing metric.
write_metric() {
  local stem="$1" dir="${METRICS_TEXTFILE_DIR:-}"
  shift
  [ -n "$dir" ] || return 0
  local tmp="${dir}/.${stem}.prom.$$"
  if ! { printf '%s\n' "$@" > "$tmp" && chmod 0644 "$tmp" && mv -f "$tmp" "${dir}/${stem}.prom"; } 2>/dev/null; then
    rm -f "$tmp" 2>/dev/null
    echo "warning: could not write the ${stem} metric to ${dir}" >&2
  fi
  return 0
}
