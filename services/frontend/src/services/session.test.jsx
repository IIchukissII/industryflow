// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { describe, test, expect } from 'vitest';
import { signOut } from './session';

const NOTEBOOKS = 'https://nb.example.com';

function recorder() {
  const calls = [];
  return {
    calls,
    origin: NOTEBOOKS,
    fetchImpl: async (url, opts) => { calls.push(['hub', url, opts]); },
    post: async (url) => { calls.push(['platform', url]); },
  };
}

describe('signOut', () => {
  test('logs out of the notebook hub before the platform session ends', async () => {
    const r = recorder();
    await signOut(r);
    expect(r.calls.map((c) => c.slice(0, 2))).toEqual([
      ['hub', `${NOTEBOOKS}/logout`],
      ['platform', '/auth/logout'],
    ]);
    // The notebook origin's cookies, and an answer the platform page cannot read.
    expect(r.calls[0][2]).toMatchObject({ credentials: 'include', mode: 'no-cors' });
  });

  test('a browser that never entered notebooks only signs out of the platform', async () => {
    const r = recorder();
    r.origin = null;
    await signOut(r);
    expect(r.calls).toEqual([['platform', '/auth/logout']]);
  });

  test('a hub that cannot be reached does not stop the platform logout', async () => {
    const r = recorder();
    r.fetchImpl = async () => { throw new TypeError('network'); };
    await signOut(r);
    expect(r.calls).toEqual([['platform', '/auth/logout']]);
  });

  test('a failed platform logout does not throw out of sign-out', async () => {
    const r = recorder();
    r.post = async () => { throw new Error('500'); };
    await expect(signOut(r)).resolves.toBeUndefined();
  });
});
