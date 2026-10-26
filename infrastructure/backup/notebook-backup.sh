#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# Authored-notebook backup on a Docker host (ADR-0038 dec 4-5).
#
# Archives each per-user work volume (ADR-0020) into the backup target, one verified archive per
# volume per run. The volumes are found by the spawner's naming (NOTEBOOK_VOLUME_TEMPLATE, the hub's
# own setting), never from the hub's database. Each is read through the Docker Engine API: a
# container is created (never started) with the volume mounted read-only and no network, its mount
# is fetched as a tar stream, and the container is removed. The job holds the Docker socket and the
# backup bucket's principal, nothing else.
#
# Archives are kept by count (RETAIN_PER_VOLUME). Once a volume no longer exists, its archives leave
# after MIRROR_RETAIN_DAYS, so a deleted tenant's notebooks do not outlive the backup's retention
# (ADR-0033 dec 4).
#
# Required env:
#   S3_BUCKET  AWS_ACCESS_KEY_ID  AWS_SECRET_ACCESS_KEY   backup target and its principal
#   NOTEBOOK_HELPER_IMAGE    an image present on the host to create the reading container from
# Optional env:
#   S3_ENDPOINT_URL          S3 endpoint                       (default: unset = AWS S3)
#   DOCKER_SOCKET            the Engine API socket             (default: /var/run/docker.sock)
#   NOTEBOOK_VOLUME_TEMPLATE the spawner's volume name         (default: industryflow-nb-{username})
#   NOTEBOOK_BACKUP_PREFIX   key prefix in S3_BUCKET           (default: notebooks)
#   RETAIN_PER_VOLUME        archives kept per volume          (default: RETAIN_PER_DB, else 14)
#   MIRROR_RETAIN_DAYS       days a gone volume's archives stay (default: RETAIN_PER_VOLUME)
#   MIRROR_NOW               epoch seconds standing in for the clock (tests)

set -euo pipefail

log() { echo "[notebook-backup $(date -u +%H:%M:%S)] $*"; }

SOCKET="${DOCKER_SOCKET:-/var/run/docker.sock}"
TEMPLATE="${NOTEBOOK_VOLUME_TEMPLATE:-industryflow-nb-{username\}}"
PREFIX="${NOTEBOOK_BACKUP_PREFIX:-notebooks}"
RETAIN="${RETAIN_PER_VOLUME:-${RETAIN_PER_DB:-14}}"
RETAIN_DAYS="${MIRROR_RETAIN_DAYS:-$RETAIN}"

# The fixed part of the spawner's name: every volume that starts with it is a work volume.
VOLUME_PREFIX="${TEMPLATE%%\{username\}*}"

_aws() { aws ${S3_ENDPOINT_URL:+--endpoint-url "$S3_ENDPOINT_URL"} "$@"; }

# _docker <method> <path> [curl args...] — the Engine API over its socket; fails on an HTTP error.
_docker() {
  local method="$1" path="$2"
  shift 2
  curl -sS --fail-with-body --unix-socket "$SOCKET" -X "$method" "$@" "http://docker${path}"
}

# Work volume names, one per line.
work_volumes() {
  _docker GET "/volumes" | jq -r --arg p "$VOLUME_PREFIX" '.Volumes // [] | .[].Name | select(startswith($p))' | sort
}

# _reader <volume> <ro|rw> — create (never start) a networkless container with the volume at /w. A
# writer's root filesystem stays writable: the Engine refuses an archive written through a read-only one.
_reader() {
  local body
  body="$(jq -nc --arg img "$NOTEBOOK_HELPER_IMAGE" --arg bind "$1:/w:$2" \
    --argjson ro "$([ "$2" = ro ] && echo true || echo false)" \
    '{Image: $img, Entrypoint: ["/bin/true"], Cmd: [], NetworkDisabled: true,
      HostConfig: {Binds: [$bind], NetworkMode: "none", ReadonlyRootfs: $ro}}')"
  _docker POST "/containers/create" -H "Content-Type: application/json" -d "$body" | jq -r .Id
}

_remove() { _docker DELETE "/containers/$1?force=true" >/dev/null || true; }

# archive_volume <volume> <file> — the volume's content as a gzip'd tar of w/…, owners preserved.
archive_volume() {
  local id
  id="$(_reader "$1" ro)"
  if ! _docker GET "/containers/${id}/archive?path=/w" | gzip -6 > "$2"; then
    _remove "$id"
    return 1
  fi
  _remove "$id"
  gzip -t "$2" && tar -tzf "$2" >/dev/null
}

# Keep the newest RETAIN archives under one volume's prefix (stamps sort chronologically).
prune_volume() {
  local keys drop
  keys="$(_aws s3api list-objects-v2 --bucket "$S3_BUCKET" --prefix "${PREFIX}/$1/" \
            --query 'Contents[].Key' --output text)"
  [ -z "$keys" ] || [ "$keys" = "None" ] && return 0
  keys="$(printf '%s\n' $keys | sort)"
  drop=$(( $(printf '%s\n' "$keys" | wc -l) - RETAIN ))
  [ "$drop" -gt 0 ] || return 0
  printf '%s\n' "$keys" | head -n "$drop" | while read -r k; do
    _aws s3 rm "s3://${S3_BUCKET}/${k}" --only-show-errors
  done
  log "  ${1}: pruned ${drop} old archive(s) (keep ${RETAIN})"
}

_stamp_epoch() {  # 20261026T182504Z -> epoch seconds
  local s="$1"
  date -u -d "${s:0:4}-${s:4:2}-${s:6:2} ${s:9:2}:${s:11:2}:${s:13:2}" +%s
}

# Archives of a volume that no longer exists leave once their newest is RETAIN_DAYS old.
age_out_gone() {
  local present="$1" now keys vol newest
  now="${MIRROR_NOW:-$(date -u +%s)}"
  keys="$(_aws s3api list-objects-v2 --bucket "$S3_BUCKET" --prefix "${PREFIX}/" \
            --query 'Contents[].Key' --output text)"
  [ -z "$keys" ] || [ "$keys" = "None" ] && return 0
  for vol in $(printf '%s\n' $keys | awk -F/ 'NF >= 3 { print $2 }' | sort -u); do
    grep -qxF "$vol" "$present" && continue
    newest="$(printf '%s\n' $keys | grep "^${PREFIX}/${vol}/" | sort | tail -n 1)"
    newest="$(basename "$newest" .tar.gz)"
    if [ $(( now - $(_stamp_epoch "$newest") )) -ge $(( RETAIN_DAYS * 86400 )) ]; then
      printf '%s\n' $keys | grep "^${PREFIX}/${vol}/" | while read -r k; do
        _aws s3 rm "s3://${S3_BUCKET}/${k}" --only-show-errors
      done
      log "  ${vol}: gone for ${RETAIN_DAYS} days, its archives removed"
    else
      log "  ${vol}: no longer exists, archives kept until ${RETAIN_DAYS} days after the last"
    fi
  done
}

main() {
  : "${S3_BUCKET:?S3_BUCKET required}" "${NOTEBOOK_HELPER_IMAGE:?NOTEBOOK_HELPER_IMAGE required}"
  [ -n "$VOLUME_PREFIX" ] || { log "NOTEBOOK_VOLUME_TEMPLATE has no fixed prefix"; exit 2; }
  export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-1}"
  local stamp vol size have n=0
  stamp="${NOTEBOOK_BACKUP_STAMP:-$(date -u +%Y%m%dT%H%M%SZ)}"
  WORK="$(mktemp -d)"
  trap 'rm -rf "$WORK"' EXIT

  log "archiving work volumes ${VOLUME_PREFIX}* -> s3://${S3_BUCKET}/${PREFIX}/ (run ${stamp})"
  work_volumes > "${WORK}/present"
  while read -r vol <&3; do
    [ -n "$vol" ] || continue
    archive_volume "$vol" "${WORK}/a.tar.gz" || { log "${vol}: could not be read"; exit 1; }
    size="$(wc -c < "${WORK}/a.tar.gz" | tr -d ' ')"
    _aws s3 cp "${WORK}/a.tar.gz" "s3://${S3_BUCKET}/${PREFIX}/${vol}/${stamp}.tar.gz" --only-show-errors
    have="$(_aws s3api head-object --bucket "$S3_BUCKET" --key "${PREFIX}/${vol}/${stamp}.tar.gz" \
              --query ContentLength --output text)"
    [ "$have" = "$size" ] || { log "${vol}: archive is ${size} bytes but ${have} in the target"; exit 1; }
    prune_volume "$vol"
    n=$((n + 1))
  done 3< "${WORK}/present"
  age_out_gone "${WORK}/present"
  log "${n} volume(s) archived"
}

# Sourcing the script (the tests do) defines the functions without running a backup.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
