<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Frontend

The manual of [ADR-0047](../../ADR/ADR-0047-one-browser-origin-for-the-app-and-every-plane-it-reaches.md): the browser app, a React single-page app built with Vite and served by the nginx edge on the origin of every API it calls.

## ADR-0047 dec 1

| Code | Role |
|---|---|
| `services/frontend/src/config.js` | Socket base URL, the page origin with its `ws`/`wss` scheme; API paths are relative |
| `services/frontend/src/services/session.js` | `assertOwnOriginPath`: `authFetch` and the axios client refuse, before sending, any URL but a path of this origin |
| `services/frontend/eslint.config.js` | `no-restricted-syntax` refuses `import.meta` in `src`, so no build variable can name an API origin |
| `services/frontend/vite.config.js` | Dev server; `server.proxy` sends `/api`, `/auth`, `/users` and `/ws` to `http://localhost:8000`, one origin in development |
| `services/frontend/Dockerfile` | `node:22-alpine` runs `npm ci && npm run build`; `nginxinc/nginx-unprivileged` serves `build/` as uid 101 |
| `services/frontend/nginx.conf.template` | Serves the app (`try_files … /index.html`) beside the API prefixes and `/ws/` |
| `services/frontend/docker-entrypoint-edge.sh` | Renders `nginx.conf.template` from the `EDGE_*` variables; refuses to start if a required one is unset |

The build takes no base-URL variable: no build can point the app's API calls at another origin.

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
| `src/index.jsx` | Entry module; components are `.jsx`, utilities `.js`, imports extensionless |
| `src/styles/theme.css` | Design tokens: colour, `--font-display` (Fraunces), `--font-sans` (IBM Plex Sans), `--font-mono` (IBM Plex Mono) |
| `src/styles/components.css` | Shared classes (`.panel`, `.kpi`, `.badge`, `.btn`, `.sdot`) |
| `src/test/setup.js` | Vitest setup |
| `public/` | Static assets at the site root (`favicon.svg`, `logo-mark.svg`) |
| `build/` | Build output (git-ignored), copied into the nginx web root |

New views use the tokens in `theme.css` instead of literal colours.

## ADR-0047 dec 2

The notebook environments are on their own origin, not a sub-path: under compose the edge serves it
on its second TLS port (`notebook-origin.conf.template`), and the app's policy frames it
([notebooks](../../docs/architecture/notebooks.md#adr-0048-dec-1-6)).

The edge serves every browser-reached plane by sub-path: [edge routes](../../docs/api/README.md#edge-routes).
Each upstream is an `EDGE_*` variable (`EDGE_API_GATEWAY`, `EDGE_ML_SERVICE`, `EDGE_TRACKING_GATEWAY`, `EDGE_OBJECT_STORE_URL`;
`EDGE_RESOLVER` defaults to the container's nameservers; `EDGE_NOTEBOOK_ORIGIN` and
`EDGE_NOTEBOOK_SSO` where notebooks are deployed), set by the `frontend` service in `docker-compose.yml` and by the
chart's `_helpers.tpl`; Kubernetes upstreams are fully qualified (`<svc>.<namespace>.svc.<clusterDomain>`),
as nginx re-resolves them per request without search domains.

| Code | Role |
|---|---|
| `services/frontend/nginx.conf.template` | Routes the upload plane and the staging area to their upstreams; security headers at server level: `Content-Security-Policy` (none under `/docs`, `/grafana/`) and `X-Frame-Options: SAMEORIGIN`, `nosniff`, referrer and permissions policy |
| `services/frontend/notebook-origin.conf.template` | The notebook origin's server, rendered only when `EDGE_NOTEBOOK_SSO` is set |
| `services/frontend/grafana.conf.template` | `/grafana/` route, rendered only when `EDGE_GRAFANA` is set; `EDGE_GRAFANA_ALLOW` source allowlist ([monitoring](../../docs/operations/monitoring.md)) |

Links: [TLS at the edge, ports and certificates](../../docs/operations/tls.md#adr-0004-dec-1),
[sign-in limits at the edge](../../docs/operations/authentication.md#adr-0004-dec-10).
