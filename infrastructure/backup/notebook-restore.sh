#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# Restore one work-volume archive (ADR-0038 dec 4, 8).
#
#   notebook-restore.sh <volume> [<stamp>|latest] [--target <volume>]
#
# Creates the target volume (default: the archived one) and writes the archive into it through the
# Docker Engine API, owners and modes as archived. A target that already exists is refused: remove it
# first, or restore under another name. The target is then read back and compared with the archive,
# entry by entry (path, type, size, owner, mode); any difference fails the restore.
#
# Env as notebook-backup.sh: S3_BUCKET, AWS_*, S3_ENDPOINT_URL, NOTEBOOK_HELPER_IMAGE, DOCKER_SOCKET,
# NOTEBOOK_BACKUP_PREFIX.

set -euo pipefail

# shellcheck source=notebook-backup.sh
source "$(dirname "$0")/notebook-backup.sh"

usage() { echo "usage: notebook-restore.sh <volume> [<stamp>|latest] [--target <volume>]" >&2; exit 2; }

# The archive's entries as "<mode> <owner> <size> <path>", the w/ root excluded, sorted. The
# timestamp columns of `tar -tv` are dropped.
_listing() {
  tar -tvzf "$1" | awk '{ name = $6; for (i = 7; i <= NF; i++) name = name " " $i
                          sub(/\/$/, "", name); if (name != "w") print $1, $2, $3, name }' | sort
}

restore_main() {
  local volume="" stamp="latest" target=""
  [ $# -ge 1 ] || usage
  volume="$1"; shift
  if [ $# -ge 1 ] && [ "$1" != "--target" ]; then stamp="$1"; shift; fi
  if [ $# -ge 1 ]; then
    [ "$1" = "--target" ] && [ $# -eq 2 ] || usage
    target="$2"
  fi
  target="${target:-$volume}"
  : "${S3_BUCKET:?S3_BUCKET required}" "${NOTEBOOK_HELPER_IMAGE:?NOTEBOOK_HELPER_IMAGE required}"
  export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-1}"

  local key id
  WORK="$(mktemp -d)"
  trap 'rm -rf "$WORK"' EXIT

  if [ "$stamp" = latest ]; then
    key="$(_aws s3api list-objects-v2 --bucket "$S3_BUCKET" --prefix "${PREFIX}/${volume}/" \
             --query 'Contents[].Key' --output text)"
    [ -n "$key" ] && [ "$key" != "None" ] || { log "no archive of ${volume}"; exit 1; }
    key="$(printf '%s\n' $key | sort | tail -n 1)"
  else
    key="${PREFIX}/${volume}/${stamp}.tar.gz"
  fi
  if _docker GET "/volumes/${target}" >/dev/null 2>&1; then
    log "volume ${target} already exists; remove it or restore with --target <new name>"
    exit 1
  fi

  log "restoring s3://${S3_BUCKET}/${key} -> volume ${target}"
  _aws s3 cp "s3://${S3_BUCKET}/${key}" "${WORK}/a.tar.gz" --only-show-errors
  gzip -t "${WORK}/a.tar.gz"
  _docker POST "/volumes/create" -H "Content-Type: application/json" \
    -d "$(jq -nc --arg n "$target" '{Name: $n}')" >/dev/null
  id="$(_reader "$target" rw)"
  if ! gzip -dc "${WORK}/a.tar.gz" | _docker PUT "/containers/${id}/archive?path=/" \
         -H "Content-Type: application/x-tar" --data-binary @- >/dev/null; then
    _remove "$id"
    log "writing into ${target} failed"
    exit 1
  fi
  _remove "$id"

  archive_volume "$target" "${WORK}/b.tar.gz"
  _listing "${WORK}/a.tar.gz" > "${WORK}/archived"
  _listing "${WORK}/b.tar.gz" > "${WORK}/restored"
  if ! diff -u "${WORK}/archived" "${WORK}/restored" > "${WORK}/diff"; then
    log "restored volume ${target} differs from the archive:"
    head -n 40 "${WORK}/diff"
    exit 1
  fi
  log "restored ${target}: $(wc -l < "${WORK}/archived") entries match the archive"
}

restore_main "$@"
