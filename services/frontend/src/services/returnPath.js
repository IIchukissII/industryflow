// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

// Where the edge sent a visitor to sign in from (ADR-0016 dec 7): the dashboards answer a request
// without a session with /login?next=<the request's path>, taken verbatim, unencoded. Only the
// dashboards' own paths are honoured, so the parameter can never send anyone off this origin or to a
// page the edge does not sign on itself.
import { refreshSession } from './session';

const DASHBOARDS = /^\/grafana\/[^\\\s]*$/;

export function returnPath(search) {
  const m = /^\?next=(.*)$/s.exec(search || '');
  if (!m) return null;
  const next = m[1];
  return DASHBOARDS.test(next) ? next : null;
}

// Back to the dashboards with a live session: refresh the access cookie first (the edge checks it on
// every dashboards request), then a full page load, since the dashboards are not part of this app.
export async function resume(next, { refresh = refreshSession, assign = (u) => window.location.assign(u) } = {}) {
  await refresh();
  assign(next);
}
