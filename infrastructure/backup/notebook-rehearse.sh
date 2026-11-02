#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# Notebook-volume restore rehearsal on a Docker host (ADR-0032 dec 6, ADR-0038 dec 8).
#
# For every volume with an archive, the latest archive is restored by notebook-restore.sh into a
# scratch volume (<REHEARSAL_VOLUME_PREFIX><volume>), which reads it back and compares it with the
# archive entry by entry; the scratch volume is then removed. Any failed restore fails the rehearsal.
#
# Env as notebook-restore.sh, plus:
#   REHEARSAL_VOLUME_PREFIX  scratch volume name prefix            (default: rehearsal-)
#   METRICS_TEXTFILE_DIR     node-exporter textfile directory      (unset: no metric)

set -euo pipefail

# shellcheck source=notebook-backup.sh
source "$(dirname "${BASH_SOURCE[0]}")/notebook-backup.sh"

log() { echo "[notebook-rehearse $(date -u +%H:%M:%S)] $*"; }

rehearse_main() {
  : "${S3_BUCKET:?S3_BUCKET required}" "${NOTEBOOK_HELPER_IMAGE:?NOTEBOOK_HELPER_IMAGE required}"
  export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-1}"
  local scratch="${REHEARSAL_VOLUME_PREFIX-rehearsal-}" keys vol target n=0 failed=0
  # A scratch name the backup would take for a work volume, or one that is the volume itself, is
  # refused: the rehearsal would archive, or remove, real work.
  if [ -z "$scratch" ] || [[ "$scratch" == "$VOLUME_PREFIX"* ]]; then
    log "REHEARSAL_VOLUME_PREFIX '${scratch}' would name a work volume"; exit 2
  fi
  WORK="$(mktemp -d)"
  trap 'rm -rf "$WORK"' EXIT

  keys="$(_aws s3api list-objects-v2 --bucket "$S3_BUCKET" --prefix "${PREFIX}/" \
            --query 'Contents[].Key' --output text)"
  [ "$keys" = "None" ] && keys=""
  for vol in $(printf '%s\n' $keys | awk -F/ 'NF >= 3 && $3 ~ /\.tar\.gz$/ { print $2 }' | sort -u); do
    target="${scratch}${vol}"
    # A scratch volume left by an interrupted rehearsal is the rehearsal's own; replace it.
    if _docker GET "/volumes/${target}" >/dev/null 2>&1; then
      _docker DELETE "/volumes/${target}" >/dev/null || { log "${target}: could not remove the leftover scratch volume"; failed=1; continue; }
    fi
    if bash "$(dirname "${BASH_SOURCE[0]}")/notebook-restore.sh" "$vol" latest --target "$target" \
         > "${WORK}/restore.log" 2>&1; then
      log "${vol}: $(grep -o '[0-9]* entries match the archive' "${WORK}/restore.log" || echo restored)"
    else
      log "${vol}: FAILED, the latest archive did not restore:"; tail -n 20 "${WORK}/restore.log"
      failed=1
    fi
    if _docker GET "/volumes/${target}" >/dev/null 2>&1 && ! _docker DELETE "/volumes/${target}" >/dev/null; then
      log "${target}: could not remove the scratch volume"; failed=1
    fi
    n=$((n + 1))
  done

  if [ "$failed" -ne 0 ]; then
    log "notebook restore rehearsal FAILED, see above"; exit 1
  fi
  # Nothing restored proves nothing (a deployment without notebooks, or a wrong prefix): no success
  # time is written.
  if [ "$n" -eq 0 ]; then
    # A success time from when archives existed would raise a stale alert with nothing to fix.
    [ -z "${METRICS_TEXTFILE_DIR:-}" ] || rm -f "${METRICS_TEXTFILE_DIR}/notebook-rehearse.prom"
    log "no notebook archive under s3://${S3_BUCKET}/${PREFIX}/; nothing rehearsed"; return 0
  fi
  write_metric notebook-rehearse \
    "# HELP industryflow_restore_rehearsal_last_success_timestamp_seconds When a restore rehearsal last passed." \
    "# TYPE industryflow_restore_rehearsal_last_success_timestamp_seconds gauge" \
    "industryflow_restore_rehearsal_last_success_timestamp_seconds{backup=\"notebooks\"} $(date -u +%s)"
  log "notebook restore rehearsal passed: ${n} volume(s) restored and compared"
}

rehearse_main "$@"
