#!/bin/sh
# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

# Render the edge config from its template before nginx starts (see nginx.conf.template).
#
# Substitutes ONLY the variables listed here, so nginx's own $host/$uri/... are never touched, and
# fails the container if any upstream is unset: an edge that silently fell back to a default name
# would proxy to a service that does not exist in this deployment (the Kubernetes Services are
# release-scoped; the compose names only resolve under compose).
set -eu

TEMPLATE="${EDGE_TEMPLATE:-/etc/nginx/edge/default.conf.template}"
OUTPUT="${EDGE_OUTPUT:-/etc/nginx/conf.d/default.conf}"
RESOLV_CONF="${EDGE_RESOLV_CONF:-/etc/resolv.conf}"

# The resolver nginx re-resolves the upstreams with: the container's own nameservers unless the
# deployment names one. Docker's embedded DNS under compose, the cluster DNS under Kubernetes.
if [ -z "${EDGE_RESOLVER:-}" ]; then
    EDGE_RESOLVER=$(awk 'BEGIN{ORS=" "} $1=="nameserver" {if ($2 ~ ":") {print "["$2"]"} else {print $2}}' "$RESOLV_CONF")
    EDGE_RESOLVER="${EDGE_RESOLVER% }"
fi
export EDGE_RESOLVER

VARS="EDGE_RESOLVER EDGE_API_GATEWAY EDGE_ML_SERVICE EDGE_NOTEBOOK_SSO EDGE_TRACKING_GATEWAY EDGE_OBJECT_STORE_URL"
missing=""
subst=""
for v in $VARS; do
    eval "val=\${$v:-}"
    # shellcheck disable=SC2154  # assigned by the eval above
    [ -n "$val" ] || missing="$missing $v"
    subst="$subst \${$v}"
done
if [ -n "$missing" ]; then
    echo "[edge] refusing to start: unset upstream variable(s):$missing" >&2
    exit 1
fi

envsubst "$subst" < "$TEMPLATE" > "$OUTPUT"

# Optional routes: rendered only when the deployment names their upstream, removed otherwise.
OPTIONAL_DIR="${EDGE_OPTIONAL_DIR:-/etc/nginx/conf.d/edge-optional}"
GRAFANA_TEMPLATE="${EDGE_GRAFANA_TEMPLATE:-/etc/nginx/edge/grafana.conf.template}"
rm -f "$OPTIONAL_DIR/grafana.conf"
if [ -n "${EDGE_GRAFANA:-}" ]; then
    # EDGE_GRAFANA_ALLOW: addresses or CIDR blocks, space- or comma-separated. Unset: no restriction.
    # An entry that is not an address refuses the start rather than widening or breaking the route.
    EDGE_GRAFANA_ACCESS=""
    set -f  # an entry is never a filename pattern
    for entry in $(printf '%s' "${EDGE_GRAFANA_ALLOW:-}" | tr ',' ' '); do
        if ! printf '%s' "$entry" | grep -Eq '^[0-9A-Fa-f:.]+(/[0-9]{1,3})?$'; then
            echo "[edge] refusing to start: EDGE_GRAFANA_ALLOW entry '$entry' is not an address or CIDR block" >&2
            exit 1
        fi
        EDGE_GRAFANA_ACCESS="$EDGE_GRAFANA_ACCESS    allow $entry;
"
    done
    set +f
    [ -z "$EDGE_GRAFANA_ACCESS" ] || EDGE_GRAFANA_ACCESS="${EDGE_GRAFANA_ACCESS}    deny all;"
    export EDGE_GRAFANA EDGE_GRAFANA_ACCESS
    envsubst '${EDGE_GRAFANA} ${EDGE_GRAFANA_ACCESS}' < "$GRAFANA_TEMPLATE" > "$OPTIONAL_DIR/grafana.conf"
    echo "[edge] dashboards at /grafana/ (upstream $EDGE_GRAFANA, allowed: ${EDGE_GRAFANA_ALLOW:-any})"
fi
echo "[edge] rendered $OUTPUT (resolver $EDGE_RESOLVER, gateway $EDGE_API_GATEWAY)"
