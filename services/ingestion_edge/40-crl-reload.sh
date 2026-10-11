#!/bin/sh
# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# Reload the edge when the device CRL changes (ADR-0007 dec 8). nginx reads ssl_crl only at
# start/reload, so a re-signed or revoked-device CRL would otherwise sit unread until the next
# restart, and the edge would hold the old list until it expired and refused every device.
# The official nginx entrypoint runs /docker-entrypoint.d/*.sh before starting nginx; this
# backgrounds a watcher and returns.
CRL=/etc/nginx/device-ca/crl/device-ca.crl
INTERVAL="${CRL_RELOAD_INTERVAL_SECONDS:-60}"

(
  last=$(cksum "$CRL" 2>/dev/null)
  while sleep "$INTERVAL"; do
    now=$(cksum "$CRL" 2>/dev/null)
    if [ -n "$now" ] && [ "$now" != "$last" ]; then
      echo "device CRL changed; reloading nginx"
      nginx -s reload && last="$now"
    fi
  done
) &
