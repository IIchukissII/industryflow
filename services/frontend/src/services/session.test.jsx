// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { describe, test, expect } from 'vitest';
import { HUB_LOGOUT_URL, signOut } from './session';

function recorder() {
  const calls = [];
  return {
    calls,
    fetchImpl: async (url, opts) => { calls.push(['hub', url, opts]); },
    post: async (url) => { calls.push(['platform', url]); },
  };
}

describe('signOut', () => {
  test('logs out of the notebook hub before the platform session ends', async () => {
    const r = recorder();
    await signOut(r);
    expect(r.calls.map((c) => c.slice(0, 2))).toEqual([
      ['hub', HUB_LOGOUT_URL],
      ['platform', '/auth/logout'],
    ]);
    expect(r.calls[0][2]).toMatchObject({ credentials: 'same-origin', redirect: 'manual' });
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
