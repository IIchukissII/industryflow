// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

// fetch() wrapper for cookie-based auth (ADR-0004 dec 3): always sends credentials so the
// httpOnly access cookie goes along, and echoes the CSRF token on unsafe methods. The
// access token is never read from JS. Paths are same-origin by default (API_URL is "").
import { API_URL } from '../config';
import { getCookie } from './api';

export async function authFetch(path, options = {}) {
  const opts = { credentials: 'include', ...options };
  const method = (opts.method || 'GET').toUpperCase();
  opts.headers = { ...(opts.headers || {}) };
  if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) {
    const csrf = getCookie('if_csrf');
    if (csrf) opts.headers['X-CSRF-Token'] = csrf;
  }
  const url = /^https?:|^wss?:/.test(path) ? path : `${API_URL}${path}`;
  return fetch(url, opts);
}
