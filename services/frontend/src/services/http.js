// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

// fetch() wrapper for cookie-based auth (ADR-0004 dec 3): always sends credentials so the
// httpOnly access cookie goes along, and echoes the CSRF token on unsafe methods. The
// access token is never read from JS. Paths are same-origin by default (API_URL is "").
// On a 401 it refreshes the session once (shared with the axios client, session.js) and retries;
// if the session cannot be refreshed the user is logged out.
import { API_URL } from '../config';
import { csrfHeaders, endSession, refreshSession, shouldRefresh } from './session';

function send(url, options) {
  const opts = { credentials: 'include', ...options };
  // Built per attempt: a refresh may rotate the CSRF cookie the retry must echo.
  opts.headers = { ...(options.headers || {}), ...csrfHeaders(opts.method) };
  return fetch(url, opts);
}

export async function authFetch(path, options = {}) {
  const absolute = /^https?:|^wss?:/.test(path);
  const url = absolute ? path : `${API_URL}${path}`;
  const res = await send(url, options);
  // Only the platform API is ours to refresh against; an absolute URL is someone else's 401.
  if (absolute || !shouldRefresh(res.status, path)) return res;
  try {
    await refreshSession();
  } catch {
    endSession();
    return res;
  }
  return send(url, options);
}

export default authFetch;
