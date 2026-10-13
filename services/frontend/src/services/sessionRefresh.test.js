// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import { AxiosError } from 'axios';

vi.mock('./session', async (importOriginal) => {
  const real = await importOriginal();
  // Navigation is not observable in jsdom; everything else is the real implementation.
  return { ...real, endSession: vi.fn() };
});

const { authFetch } = await import('./http');
const { default: api } = await import('./api');
const session = await import('./session');

const json = (status, body = {}) => new Response(JSON.stringify(body), { status });

// One fetch stub serving both the API and the refresh endpoint, counting refreshes.
function stubFetch({ refreshOk = true } = {}) {
  let refreshed = false;
  const calls = { refresh: 0, api: [] };
  globalThis.fetch = vi.fn(async (url, opts) => {
    if (String(url).endsWith('/auth/refresh')) {
      calls.refresh += 1;
      await new Promise((r) => setTimeout(r, 10)); // keep it in flight while others arrive
      refreshed = refreshOk;
      return json(refreshOk ? 200 : 401);
    }
    calls.api.push({ url, opts });
    return refreshed ? json(200, { ok: true }) : json(401);
  });
  return calls;
}

// The axios client goes through an adapter, not fetch; route it into the same stub.
function routeAxiosThroughFetch() {
  api.defaults.adapter = async (config) => {
    const res = await globalThis.fetch(config.url, { method: config.method });
    const response = { data: await res.json(), status: res.status, headers: {}, config };
    if (res.status >= 400) {
      throw new AxiosError('failed', 'ERR_BAD_REQUEST', config, null, response);
    }
    return response;
  };
}

beforeEach(() => {
  session.endSession.mockClear();
  document.cookie = 'if_csrf=tok1';
});
afterEach(() => {
  delete globalThis.fetch;
});

describe('authFetch', () => {
  test('a 401 refreshes the session and retries once', async () => {
    const calls = stubFetch();
    const res = await authFetch('/api/alerts');
    expect(res.status).toBe(200);
    expect(calls.refresh).toBe(1);
    expect(calls.api).toHaveLength(2);
    expect(session.endSession).not.toHaveBeenCalled();
  });

  test('a failed refresh ends the session', async () => {
    stubFetch({ refreshOk: false });
    const res = await authFetch('/api/alerts');
    expect(res.status).toBe(401);
    expect(session.endSession).toHaveBeenCalledTimes(1);
  });

  test('an auth endpoint 401 is an answer, not a lapsed session', async () => {
    const calls = stubFetch();
    const res = await authFetch('/auth/login', { method: 'POST' });
    expect(res.status).toBe(401);
    expect(calls.refresh).toBe(0);
  });

  test('the retry echoes the CSRF token current after the refresh', async () => {
    const calls = stubFetch();
    globalThis.fetch.mockImplementationOnce(async () => {
      document.cookie = 'if_csrf=tok2'; // the server rotated it with the refresh
      return json(401);
    });
    await authFetch('/api/alert-rules', { method: 'POST' });
    const retry = calls.api.at(-1);
    expect(retry.opts.headers['X-CSRF-Token']).toBe('tok2');
  });
});

describe('both clients share one refresh', () => {
  test('concurrent 401s from fetch and axios send a single refresh', async () => {
    const calls = stubFetch();
    routeAxiosThroughFetch();
    const results = await Promise.all([
      authFetch('/api/alerts'),
      authFetch('/api/equipment'),
      api.get('/api/cache/sensors'),
    ]);
    expect(results[0].status).toBe(200);
    expect(results[1].status).toBe(200);
    expect(results[2].status).toBe(200);
    expect(calls.refresh).toBe(1);
  });

  test('axios ends the session when the refresh fails', async () => {
    stubFetch({ refreshOk: false });
    routeAxiosThroughFetch();
    await expect(api.get('/api/cache/sensors')).rejects.toBeTruthy();
    expect(session.endSession).toHaveBeenCalledTimes(1);
  });
});
