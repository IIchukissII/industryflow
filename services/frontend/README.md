<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Frontend

The web app: a React single-page app built with Vite, served by the nginx edge same-origin with the API ([ADR-0004](../../ADR/ADR-0004-api-authentication-sessions-and-transport.md), [ADR-0009](../../ADR/ADR-0009-kubernetes-deployment-and-packaging.md)).

## Commands

| Task | Command |
|---|---|
| Install | `npm install` |
| Dev server on `:3000` | `npm run dev` |
| Production build to `build/` | `npm run build` |
| Serve the build locally | `npm run preview` |
| Lint | `npm run lint` (`eslint src`) |
| Tests (Vitest, jsdom) | `npm test` |
| Tests, watch mode | `npm run test:watch` |

The dev server proxies `/api`, `/auth`, `/users` and `/ws` to `http://localhost:8000`
(`server.proxy` in `vite.config.js`).

| Name (build time) | Default | Meaning |
|---|---|---|
| `VITE_API_URL` | empty (same-origin) | API base URL |
| `VITE_WS_URL` | derived from the page origin | WebSocket base URL |

## Layout

| Path | Content |
|---|---|
| `index.html` | Entry page; loads `/src/index.jsx` |
| `src/` | Components (`.jsx`), utilities (`config.js`, `services/*.js`); imports are extensionless |
| `src/styles/theme.css` | Design tokens: colour, `--font-display` (Fraunces), `--font-sans` (IBM Plex Sans), `--font-mono` (IBM Plex Mono) |
| `src/styles/components.css` | Shared classes (`.panel`, `.kpi`, `.badge`, `.btn`, `.sdot`) |
| `src/test/setup.js` | Vitest setup |
| `public/` | Static assets at the site root (`favicon.svg`, `logo-mark.svg`) |
| `build/` | Build output (git-ignored), copied into the nginx web root |

Build-time variables are read as `import.meta.env.VITE_*`. New views use the tokens in `theme.css`
instead of literal colours.

## Container

| File | Role |
|---|---|
| `Dockerfile` | `node:22-alpine` runs `npm ci && npm run build`; `nginxinc/nginx-unprivileged` serves `build/` as uid 101 |
| `docker-entrypoint-tls.sh` | Generates a self-signed `/etc/nginx/certs/tls.{crt,key}` when none is mounted; `CERT_EXTRA_SAN` adds SANs |
| `docker-entrypoint-edge.sh` | Renders `nginx.conf.template` from the `EDGE_*` variables; refuses to start if one is unset |
| `nginx.conf.template` | Routes the API prefixes, `/ws/`, `/jupyter/`, the upload plane and the SPA (`try_files … /index.html`); sets HSTS, CSP, frame/sniff/referrer/permissions policy |

| Port | Serves |
|---|---|
| `8080` | Plain HTTP; `/healthz`. Requests without `X-Forwarded-Proto: https` are redirected to HTTPS |
| `8443` | HTTPS |

### Edge upstreams

| Variable | Compose | Helm |
|---|---|---|
| `EDGE_API_GATEWAY` | `api-gateway:8000` | api-gateway Service FQDN |
| `EDGE_ML_SERVICE` | `ml-service-api:8002` | ml-service-api Service FQDN |
| `EDGE_NOTEBOOK_SSO` | `notebook-sso:8080` | notebook SSO proxy Service FQDN |
| `EDGE_TRACKING_GATEWAY` | `notebook-tracking-gateway:5050` | tracking gateway Service FQDN |
| `EDGE_OBJECT_STORE_URL` | `${MINIO_ENDPOINT:-http://minio:9000}` | in-cluster MinIO Service URL, or `infra.minio.external.endpoint` |
| `EDGE_RESOLVER` | the container's nameservers | same |

Kubernetes upstreams are fully qualified (`<svc>.<namespace>.svc.<clusterDomain>`): nginx re-resolves
them per request, and the resolver applies no search domains.

### Routes

[API reference, edge routes](../../docs/api/README.md#edge-routes).
