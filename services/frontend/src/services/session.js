// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { notebookOrigin } from './notebooks';

// Platform sign-out, including the embedded notebook hub (ADR-0014 dec 7, ADR-0012 dec 6).
//
// The hub keeps a login of its own once a notebook session has opened it. Signing out of the platform
// therefore signs out of the hub too, first, while the notebook session still admits: logging out of
// the hub stops the user's notebook server, and stopping it revokes its credentials. The hub is on the
// notebook origin (ADR-0048), so the request carries that origin's cookies and its answer is not read.
// The platform's own logout then ends the session, which ends the notebook session with it, and
// revokes every notebook credential of the user server-side, so a sign-out from a closed tab or a
// failed hub call still ends them.

export async function signOut({ post, fetchImpl = globalThis.fetch, origin = notebookOrigin() }) {
  if (origin) {
    try {
      await fetchImpl(`${origin}/logout`, { mode: 'no-cors', credentials: 'include' });
    } catch {
      /* notebooks not deployed, or the hub is down: the platform logout below still revokes */
    }
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

// Credentials go only to this origin (ADR-0047 dec 1): a path with one leading slash, not `//` or
// `/\`, which browsers read as another host. Both clients refuse anything else before sending.
export function assertOwnOriginPath(path) {
  if (!/^\/(?![/\\])/.test(path)) throw new TypeError(`Only a path of this origin is sent, not ${path}`);
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
    inflight = fetch('/auth/refresh', {
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
