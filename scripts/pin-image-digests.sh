#!/bin/sh
# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# Resolve the immutable digests of the first-party IndustryFlow images for a given tag and
# emit a Helm values overlay that pins each by digest (ADR-0009).
#
# First-party image digests rotate on every build, so they are NOT hand-maintained in
# values.yaml — values.yaml ships `digest: ""` (use the mutable tag, the dev default) and a
# digest, when present, wins. This script generates the production overlay:
#
#   scripts/pin-image-digests.sh latest > deploy/helm/industryflow/values-digests.yaml
#   helm upgrade --install if deploy/helm/industryflow -f values-digests.yaml
#
# The image list is derived from values.yaml (every mapping with `image` + `digest`), so an image
# added to the chart is pinned without touching this script. Resolution uses the registry HTTP
# API (no docker/skopeo/crane); private GHCR packages authenticate via GITHUB_TOKEN or
# `gh auth token`, public ones resolve anonymously. Needs python3 with PyYAML.
#
# Usage:  pin-image-digests.sh [TAG]                 (TAG defaults to "latest")
# Env:    REGISTRY  registry/namespace prefix         (default: the chart's image.registry)
#         GITHUB_TOKEN  PAT for private package pulls  (else falls back to `gh auth token`)

set -eu
exec python3 "$(dirname "$0")/pin_image_digests.py" "$@"
