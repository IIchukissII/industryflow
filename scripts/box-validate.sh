#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# Box validation (ADR-0026 dec 8, 9): runs the end-to-end suite on the long-lived environment over SSH
# and posts its result as the `box-e2e` status of the commit deployed there. A versioned release is cut
# only from a commit whose status is success (.github/workflows/release.yml).
#
#   scripts/box-validate.sh <ssh-target> [compose-dir]      e.g. scripts/box-validate.sh me@box ~/industryflow
#
# Needs `gh` signed in with permission to post commit statuses. The environment must already run the
# commit, deployed by upgrade from the last box-validated one; the suite checks what is deployed.
set -euo pipefail
target="${1:?usage: box-validate.sh <ssh-target> [compose-dir]}"
dir="${2:-industryflow}"

log="$(mktemp)"
trap 'rm -f "$log"' EXIT
ssh "$target" "cd $dir && scripts/box-e2e.sh" 2>&1 | tee "$log" || true
read -r tag sha result < <(grep -E '^box-e2e [0-9a-f]{40} (passed|failed)$' "$log" | tail -1) || {
    echo "box-validate: the run printed no result line; no status posted" >&2; exit 1; }
[ "$tag" = box-e2e ]
state=failure; [ "$result" = passed ] && state=success
gh api "repos/{owner}/{repo}/statuses/$sha" -f state="$state" -f context=box-e2e \
    -f description="End-to-end suite on the long-lived environment: $result" >/dev/null
echo "box-validate: $sha box-e2e=$state"
[ "$state" = success ]
