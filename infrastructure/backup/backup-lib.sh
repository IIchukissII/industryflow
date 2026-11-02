#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# Shared by the backup and rehearsal scripts (sourced, never run).

# copy_rows — reads a data-only SQL dump on stdin and prints "<schema>.<table>\t<rows>" per table
# (the table as pg_dump names it): the lines of each COPY block, one per row (a newline inside a value
# is escaped). A backup and its restored copy are both read through it, so both are counted as COPY
# reads them: each relation's own rows, a compressed chunk's in its compressed table, with no planner
# in between.
copy_rows() {
  awk -v OFS='\t' '
    copying && $0 == "\\." { print table, rows; copying = 0; next }
    copying { rows++; next }
    /^COPY .* FROM stdin;$/ {
      # The table as written, a quoted identifier included: COPY <name> [(<columns>)] FROM stdin;
      match($0, /^COPY ("([^"]|"")*"|[^ ."]+)(\.("([^"]|"")*"|[^ ."]+))?/)
      table = substr($0, 6, RLENGTH - 5); rows = 0; copying = 1
    }'
}

# compared_tables — keeps the lines of a row listing that a rehearsal compares. Of TimescaleDB's own
# tables, the structure a restore must bring back (hypertables, chunks and their slices, constraints,
# continuous aggregates) and the chunks are compared; what its scheduler writes after a restore (job
# statistics, telemetry, metadata) and its job configuration are not.
compared_tables() {
  awk -F'\t' '$1 ~ /^_timescaledb_catalog\./ {
                 if ($1 !~ /\.(hypertable|chunk|dimension|dimension_slice|chunk_constraint|continuous_agg)$/) next
               }
               $1 ~ /^_timescaledb_config\./ { next }
               $1 ~ /^_timescaledb_internal\./ && $1 !~ /_chunk"?$/ { next }
               { print }' | sort
}

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
