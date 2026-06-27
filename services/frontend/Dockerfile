# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

# ---- build stage ----
FROM node:26-alpine AS build
WORKDIR /app

# Install dependencies from the lockfile (reproducible)
COPY package.json package-lock.json ./
RUN npm ci

# Build the app. By default the API base URLs are empty (same-origin), so the app talks to
# the nginx proxy in front of it; override these build args for a direct/dev setup.
COPY . .
ARG REACT_APP_API_URL=""
ARG REACT_APP_ALERT_URL=""
ARG REACT_APP_ML_URL=""
ARG REACT_APP_WS_URL=""
ENV REACT_APP_API_URL=$REACT_APP_API_URL \
    REACT_APP_ALERT_URL=$REACT_APP_ALERT_URL \
    REACT_APP_ML_URL=$REACT_APP_ML_URL \
    REACT_APP_WS_URL=$REACT_APP_WS_URL
RUN npm run build

# ---- serve stage ----
# Unprivileged nginx: runs as uid 101 and listens on high ports (8080/8443), so the edge
# needs no root and no NET_BIND_SERVICE. apk/copy/chown need root; we drop back to 101 at the end.
FROM nginxinc/nginx-unprivileged:1.27-alpine
USER root
RUN apk add --no-cache openssl
COPY nginx.conf /etc/nginx/conf.d/default.conf
# TLS cert bootstrap runs before nginx starts (the image runs /docker-entrypoint.d/*.sh).
COPY docker-entrypoint-tls.sh /docker-entrypoint.d/00-tls.sh
# Make the cert dir writable by uid 101 so the dev self-sign works (a compose named volume or
# k8s emptyDir mounted here inherits this ownership); a real cert mounted here overrides it.
RUN chmod +x /docker-entrypoint.d/00-tls.sh \
 && mkdir -p /etc/nginx/certs && chown -R 101:101 /etc/nginx/certs
COPY --from=build /app/build /usr/share/nginx/html
USER 101
EXPOSE 8080 8443
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO- http://127.0.0.1:8080/healthz >/dev/null 2>&1 || exit 1
CMD ["nginx", "-g", "daemon off;"]
