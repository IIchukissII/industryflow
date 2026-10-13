// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

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
