# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
# SPDX-License-Identifier: AGPL-3.0-or-later

# ---- build stage ----
FROM node:20-alpine AS build
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
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/build /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO- http://localhost/ >/dev/null 2>&1 || exit 1
CMD ["nginx", "-g", "daemon off;"]
