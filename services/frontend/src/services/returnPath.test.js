// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { describe, test, expect } from 'vitest';
import { returnPath, resume } from './returnPath';

describe('returnPath', () => {
  test('takes the dashboards path the edge sent, query included, verbatim', () => {
    expect(returnPath('?next=/grafana/d/abc/incidents?orgId=1&from=now-6h'))
      .toBe('/grafana/d/abc/incidents?orgId=1&from=now-6h');
    expect(returnPath('?next=/grafana/')).toBe('/grafana/');
  });

  test.each([
    '', '?', '?next=', '?next=/', '?next=/settings', '?next=//evil.example/grafana/',
    '?next=https://evil.example/grafana/', '?next=/grafana', '?next=/grafana/\\evil',
    '?next=/grafana/ x', '?other=1&next=/grafana/', '?next=%2Fgrafana%2F', '?next=/jupyter/hub/',
  ])('refuses %j', (search) => {
    expect(returnPath(search)).toBeNull();
  });
});

describe('resume', () => {
  test('refreshes the session before the page load, and loads nothing if it cannot', async () => {
    const calls = [];
    await resume('/grafana/', { refresh: async () => calls.push('refresh'), assign: (u) => calls.push(u) });
    expect(calls).toEqual(['refresh', '/grafana/']);

    const none = [];
    await expect(resume('/grafana/', {
      refresh: async () => { throw new Error('expired'); }, assign: (u) => none.push(u),
    })).rejects.toThrow('expired');
    expect(none).toEqual([]);
  });
});
