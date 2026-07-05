<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: AGPL-3.0-or-later
-->

# frontend

The IndustryFlow web app — a React single-page app built with **Vite**, served in production by
nginx behind the TLS edge (same-origin with the API, so cookies/CSRF are first-party — ADR-0004 /
ADR-0009).

## Develop

```bash
npm install
npm run dev        # Vite dev server on :3000, proxies /api,/auth,/users,/ws to a local gateway
npm run build      # production build → build/
npm run preview    # serve the production build locally
npm run lint       # eslint src --ext .js,.jsx
```

Point the dev server at a running gateway by editing the `server.proxy` targets in
`vite.config.js` (default `http://localhost:8000`), or override the base URLs with `VITE_API_URL` /
`VITE_WS_URL` for a non-proxied setup. By default the URLs are empty, i.e. **same-origin**.

## Layout & conventions

- **`src/`** — React components are `.jsx`; the plain-JS utilities (`config.js`, `services/*.js`)
  stay `.js`. Imports are extensionless (Vite resolves `.jsx`/`.js`).
- **`index.html`** lives at the project root (Vite convention) and loads `/src/index.jsx`.
- **`public/`** — static assets served at the site root (`favicon.svg`, `logo-mark.svg`).
- **Styling** — a shared token system in `src/styles/theme.css` (graphite + signal-blue palette,
  Space Grotesk / IBM Plex) and shared classes in `src/styles/components.css` (`.panel`, `.kpi`,
  `.badge`, `.btn`, `.sdot`). New views reuse these tokens rather than hardcoding colours.
- **Env** — build-time vars are `import.meta.env.VITE_*` (not `process.env`).
- **Output** — `npm run build` emits to `build/` (not Vite's default `dist/`), which the
  `Dockerfile` copies into the nginx web root; `build/` is git-ignored.

## Build & serve (container)

The multi-stage `Dockerfile` runs `npm ci && npm run build` on `node:20-alpine`, then copies
`build/` into `nginxinc/nginx-unprivileged`. `nginx.conf` reverse-proxies the API prefixes to the
gateway/ml-service and serves the SPA (`try_files … /index.html`).
