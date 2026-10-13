# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

# ---- build stage ----
FROM node:22-alpine AS build
WORKDIR /app

# Install dependencies from the lockfile (reproducible)
COPY package.json package-lock.json ./
RUN npm ci

# Build the app. The image is always served behind this edge, same-origin with the API, so it
# takes no base-URL build args: src/config.js defaults to same-origin. VITE_* overrides are for
# `npm run dev` against a gateway elsewhere (see README).
COPY . .
RUN npm run build

# ---- serve stage ----
# Unprivileged nginx: runs as uid 101 and listens on high ports (8080/8443), so the edge
# needs no root and no NET_BIND_SERVICE. apk/copy/chown need root; we drop back to 101 at the end.
FROM nginxinc/nginx-unprivileged:1.31-alpine
USER root
RUN apk add --no-cache openssl
# The edge config is a TEMPLATE: 10-edge-config.sh renders it into conf.d at start from the
# EDGE_* upstreams the deployment passes (compose: service names; Helm: the release's Service
# FQDNs), and refuses to start without them. It is kept out of /etc/nginx/templates so the stock
# envsubst step, which substitutes every environment variable, never touches it.
COPY nginx.conf.template /etc/nginx/edge/default.conf.template
# TLS cert bootstrap and the config render run before nginx starts (/docker-entrypoint.d/*.sh).
COPY docker-entrypoint-tls.sh /docker-entrypoint.d/00-tls.sh
COPY docker-entrypoint-edge.sh /docker-entrypoint.d/10-edge-config.sh
# Make the cert dir writable by uid 101 so the dev self-sign works (a compose named volume or
# k8s emptyDir mounted here inherits this ownership); a real cert mounted here overrides it. The
# rendered config is written over the stock conf.d/default.conf, which the base already gives 101.
RUN chmod +x /docker-entrypoint.d/00-tls.sh /docker-entrypoint.d/10-edge-config.sh \
 && mkdir -p /etc/nginx/certs && chown -R 101:101 /etc/nginx/certs /etc/nginx/conf.d
COPY --from=build /app/build /usr/share/nginx/html
USER 101
EXPOSE 8080 8443
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO- http://127.0.0.1:8080/healthz >/dev/null 2>&1 || exit 1
CMD ["nginx", "-g", "daemon off;"]
