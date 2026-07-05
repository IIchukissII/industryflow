// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

// Central API base URLs. By default they are empty, i.e. **same-origin** — the app is
// served behind a reverse proxy (nginx / ingress) that forwards /api, /auth, /users, /ws
// to the gateway, so requests are first-party and cookies/CSRF work without CORS
// (ADR-0004, ADR-0009). Override with build-time VITE_* vars for a direct/dev setup
// (e.g. VITE_API_URL=http://localhost:8000 when running `npm run dev`).
export const API_URL = import.meta.env.VITE_API_URL || '';
export const WS_URL = import.meta.env.VITE_WS_URL ||
  (typeof window !== 'undefined'
    ? `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}`
    : '');
