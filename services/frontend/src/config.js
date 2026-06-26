// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

// Central API base URLs. By default they are empty, i.e. **same-origin** — the app is
// served behind a reverse proxy (nginx / ingress) that forwards /api, /auth, /users, /ws
// to the gateway, so requests are first-party and cookies/CSRF work without CORS
// (ADR-0004, ADR-0009). Override with build-time REACT_APP_* vars for a direct/dev setup
// (e.g. REACT_APP_API_URL=http://localhost:8000 when running `npm start`).
export const API_URL = process.env.REACT_APP_API_URL || '';
export const WS_URL = process.env.REACT_APP_WS_URL ||
  (typeof window !== 'undefined'
    ? `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}`
    : '');
