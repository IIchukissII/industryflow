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

# The per-source limit on password sign-in (ADR-0004 dec 10): on where the edge sees the clients' own
# addresses, off behind a proxy that reaches it from its own (a Kubernetes Ingress).
EDGE_SIGN_IN_PER_SOURCE="${EDGE_SIGN_IN_PER_SOURCE:-on}"
case "$EDGE_SIGN_IN_PER_SOURCE" in
    on|off) ;;
    *) echo "[edge] refusing to start: EDGE_SIGN_IN_PER_SOURCE must be on or off" >&2; exit 1 ;;
esac
export EDGE_SIGN_IN_PER_SOURCE

# The notebook origin (ADR-0048): the one other origin the app frames, posts its ticket to and signs
# out of. Unset: notebooks are not deployed and the app frames nothing. It must be an origin and nothing
# more, since it is written into the Content-Security-Policy.
if [ -n "${EDGE_NOTEBOOK_ORIGIN:-}" ]; then
    if ! printf '%s' "$EDGE_NOTEBOOK_ORIGIN" | grep -Eq '^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?$'; then
        echo "[edge] refusing to start: EDGE_NOTEBOOK_ORIGIN must be https://host[:port]" >&2
        exit 1
    fi
    EDGE_FRAME_SOURCES="$EDGE_NOTEBOOK_ORIGIN"
    EDGE_NOTEBOOK_SOURCES=" $EDGE_NOTEBOOK_ORIGIN"
else
    EDGE_FRAME_SOURCES="'none'"
    EDGE_NOTEBOOK_SOURCES=""
fi
export EDGE_FRAME_SOURCES EDGE_NOTEBOOK_SOURCES

VARS="EDGE_RESOLVER EDGE_SIGN_IN_PER_SOURCE EDGE_FRAME_SOURCES EDGE_NOTEBOOK_SOURCES EDGE_API_GATEWAY EDGE_ML_SERVICE EDGE_TRACKING_GATEWAY EDGE_OBJECT_STORE_URL"
missing=""
subst=""
for v in $VARS; do
    eval "val=\${$v:-}"
    # shellcheck disable=SC2154  # assigned by the eval above
    # The notebook sources are empty where notebooks are not deployed.
    [ -n "$val" ] || [ "$v" = EDGE_NOTEBOOK_SOURCES ] || missing="$missing $v"
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
# The notebook origin's own server (ADR-0048 dec 6), where this edge serves it: a second TLS port on
# the same host and certificate, for an install reached by address or local name. Where the origin is a
# sibling host, the deployment's ingress serves it and names no EDGE_NOTEBOOK_SSO here.
NOTEBOOK_TEMPLATE="${EDGE_NOTEBOOK_TEMPLATE:-/etc/nginx/edge/notebook-origin.conf.template}"
NOTEBOOK_OUTPUT="${EDGE_NOTEBOOK_OUTPUT:-/etc/nginx/conf.d/notebook-origin.conf}"
rm -f "$NOTEBOOK_OUTPUT"
if [ -n "${EDGE_NOTEBOOK_SSO:-}" ]; then
    if [ -z "${EDGE_NOTEBOOK_ORIGIN:-}" ]; then
        echo "[edge] refusing to start: EDGE_NOTEBOOK_SSO is set without EDGE_NOTEBOOK_ORIGIN" >&2
        exit 1
    fi
    export EDGE_NOTEBOOK_SSO
    envsubst '${EDGE_RESOLVER} ${EDGE_NOTEBOOK_SSO}' < "$NOTEBOOK_TEMPLATE" > "$NOTEBOOK_OUTPUT"
    echo "[edge] notebook origin $EDGE_NOTEBOOK_ORIGIN on :8444 (upstream $EDGE_NOTEBOOK_SSO)"
fi
echo "[edge] rendered $OUTPUT (resolver $EDGE_RESOLVER, gateway $EDGE_API_GATEWAY)"
