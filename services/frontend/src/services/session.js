// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { API_URL } from '../config';

// Platform sign-out, including the embedded notebook hub (ADR-0014, ADR-0012 dec 6).
//
// The hub keeps a login of its own once the platform session has opened it. Signing out of the
// platform therefore signs out of the hub too — first, because the hub is reachable only through
// the SSO proxy, which stops letting anything through once the platform session is gone. Logging
// out of the hub stops the user's notebook server, and stopping it revokes its credentials. The
// platform's own logout then revokes every notebook credential of the user server-side as well, so
// a sign-out from a closed tab or a failed hub call still ends them.

export const HUB_LOGOUT_URL = '/jupyter/hub/logout';

export async function signOut({ post, fetchImpl = globalThis.fetch }) {
  try {
    // `manual`: the hub answers with a redirect to its login page, which is not ours to follow.
    await fetchImpl(HUB_LOGOUT_URL, { credentials: 'same-origin', redirect: 'manual' });
  } catch {
    /* notebooks not deployed, or the hub is down: the platform logout below still revokes */
  }
  try {
    await post('/auth/logout');
  } catch {
    /* best-effort revoke */
  }
}

// The session lifecycle both HTTP clients share (ADR-0004 dec 3). The access token is a short-lived
// httpOnly cookie, so any request may come back 401 when it lapses; the client then refreshes the
// session once and retries. ONE implementation for the axios client (api.js) and the fetch wrapper
// (http.js), and ONE refresh in flight at a time: a page that fires several requests as the token
// lapses must not send several refreshes, because a rotating refresh token is spent by the first
// and the rest would then fail and log the user out.

export function getCookie(name) {
  const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
  return m ? m.pop() : '';
}

// The JS-readable CSRF token echoed on state-changing requests (double-submit).
export function csrfHeaders(method) {
  const m = (method || 'GET').toUpperCase();
  if (!['POST', 'PUT', 'PATCH', 'DELETE'].includes(m)) return {};
  const csrf = getCookie('if_csrf');
  return csrf ? { 'X-CSRF-Token': csrf } : {};
}

// A 401 from the auth endpoints themselves is an answer, not an expired session (a wrong password,
// a spent refresh token); refreshing on it would loop.
export function shouldRefresh(status, url) {
  return status === 401 && !String(url || '').includes('/auth/');
}

let inflight = null;

// Refresh the session; concurrent callers share the one request. Resolves when the session is
// renewed, rejects when it cannot be.
export function refreshSession() {
  if (!inflight) {
    inflight = fetch(`${API_URL}/auth/refresh`, {
      method: 'POST',
      credentials: 'include',
      headers: csrfHeaders('POST'),
    })
      .then((res) => {
        if (!res.ok) throw new Error(`session refresh failed (${res.status})`);
      })
      .finally(() => {
        inflight = null;
      });
  }
  return inflight;
}

// The session is over: drop the cached user and go to the login page.
export function endSession() {
  localStorage.removeItem('user');
  if (window.location.pathname !== '/login') window.location.href = '/login';
}
