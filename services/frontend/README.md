<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Frontend

The manual of [ADR-0047](../../ADR/ADR-0047-one-browser-origin-for-the-app-and-every-plane-it-reaches.md): the browser app, a React single-page app built with Vite and served by the nginx edge on the origin of every API it calls.

## ADR-0047 dec 1

| Code | Role |
|---|---|
| `services/frontend/src/config.js` | API and socket base URLs; empty by default, so every call is same-origin |
| `services/frontend/vite.config.js` | Dev server; `server.proxy` sends `/api`, `/auth`, `/users` and `/ws` to `http://localhost:8000`, one origin in development |
| `services/frontend/Dockerfile` | `node:22-alpine` runs `npm ci && npm run build`; `nginxinc/nginx-unprivileged` serves `build/` as uid 101 |
| `services/frontend/nginx.conf.template` | Serves the app (`try_files … /index.html`) beside the API prefixes and `/ws/` |
| `services/frontend/docker-entrypoint-edge.sh` | Renders `nginx.conf.template` from the `EDGE_*` variables; refuses to start if a required one is unset |

| Name (build time) | Default | Meaning |
|---|---|---|
| `VITE_API_URL` | empty (same-origin) | API base URL |
| `VITE_WS_URL` | derived from the page origin | WebSocket base URL |

Build-time variables are read as `import.meta.env.VITE_*`.

### Commands

| Task | Command |
|---|---|
| Install | `npm install` |
| Dev server on `:3000` | `npm run dev` |
| Production build to `build/` | `npm run build` |
| Serve the build locally | `npm run preview` |
| Lint | `npm run lint` (`eslint src`) |
| Tests (Vitest, jsdom) | `npm test` |
| Tests, watch mode | `npm run test:watch` |

### Layout

| Path | Content |
|---|---|
| `index.html` | Entry page; loads `/src/index.jsx` |
| `src/` | Components (`.jsx`), utilities (`config.js`, `services/*.js`); imports are extensionless |
| `src/styles/theme.css` | Design tokens: colour, `--font-display` (Fraunces), `--font-sans` (IBM Plex Sans), `--font-mono` (IBM Plex Mono) |
| `src/styles/components.css` | Shared classes (`.panel`, `.kpi`, `.badge`, `.btn`, `.sdot`) |
| `src/test/setup.js` | Vitest setup |
| `public/` | Static assets at the site root (`favicon.svg`, `logo-mark.svg`) |
| `build/` | Build output (git-ignored), copied into the nginx web root |

New views use the tokens in `theme.css` instead of literal colours.

## ADR-0047 dec 2

The edge serves every browser-reached plane by sub-path: [edge routes](../../docs/api/README.md#edge-routes).
Each upstream is an `EDGE_*` variable (`EDGE_API_GATEWAY`, `EDGE_ML_SERVICE`, `EDGE_NOTEBOOK_SSO`,
`EDGE_TRACKING_GATEWAY`, `EDGE_OBJECT_STORE_URL`; `EDGE_RESOLVER` defaults to the container's
nameservers), set by the `frontend` service in `docker-compose.yml` and by the
chart's `_helpers.tpl`; Kubernetes upstreams are fully qualified (`<svc>.<namespace>.svc.<clusterDomain>`),
as nginx re-resolves them per request without search domains.

| Code | Role |
|---|---|
| `services/frontend/nginx.conf.template` | Routes `/jupyter/`, the upload plane and the staging area to their upstreams; security headers at server level: `Content-Security-Policy` and `X-Frame-Options: SAMEORIGIN` on the app (none under `/jupyter/`, `/docs`, `/grafana/`), `nosniff`, referrer and permissions policy |
| `services/frontend/grafana.conf.template` | `/grafana/` route ([monitoring](../../docs/operations/monitoring.md)) |

Links: [TLS at the edge, ports and certificates](../../docs/operations/tls.md#adr-0004-dec-1),
[sign-in limits at the edge](../../docs/operations/authentication.md#adr-0004-dec-10).
