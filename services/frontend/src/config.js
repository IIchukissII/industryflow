// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

// Every API the app calls is on its own origin (ADR-0047 dec 1): API paths are relative, and the
// socket base is the page origin with its ws/wss scheme. Under `npm run dev`, vite's proxy keeps it one origin.
export const WS_URL = typeof window !== 'undefined'
  ? `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}`
  : '';
