// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { describe, test, expect } from 'vitest';
import {
  dedupeAscending, toSecondPoints, resolutionFor, resolutionFits, windowOf, WINDOWS, POINTS_PER_REQUEST,
} from './series';

describe('dedupeAscending', () => {
  test('sorts points ascending by time', () => {
    const out = dedupeAscending([{ time: 3, value: 'c' }, { time: 1, value: 'a' }, { time: 2, value: 'b' }]);
    expect(out.map((p) => p.time)).toEqual([1, 2, 3]);
  });
  test('collapses duplicate timestamps, last write wins', () => {
    const out = dedupeAscending([{ time: 1, value: 'a' }, { time: 1, value: 'b' }]);
    expect(out).toEqual([{ time: 1, value: 'b' }]);
  });
  test('empty input → empty output', () => {
    expect(dedupeAscending([])).toEqual([]);
  });
});

describe('toSecondPoints', () => {
  test('readings within one second become one point, the last of them', () => {
    const rows = [
      { time: '2026-11-26T10:00:00.900Z', value: 3 },
      { time: '2026-11-26T10:00:00.100Z', value: 1 },
      { time: '2026-11-26T10:00:01.000Z', value: 4 },
      { time: '2026-11-26T10:00:00.500Z', value: 2 },
    ];
    const { points, merged } = toSecondPoints(rows);
    const t0 = Date.parse('2026-11-26T10:00:00Z') / 1000;
    expect(points).toEqual([{ time: t0, value: 3 }, { time: t0 + 1, value: 4 }]);
    expect(merged).toBe(2);
  });
  test('microsecond-spaced readings, as a load test sends them, fold into their second', () => {
    const rows = Array.from({ length: 500 }, (_, i) => ({ time: `2026-11-26T10:00:00.${String(i).padStart(6, '0')}Z`, value: i }));
    const { points, merged } = toSecondPoints(rows);
    expect(points).toHaveLength(1);
    expect(merged).toBe(499);
  });
});

describe('resolution of a window', () => {
  test('auto follows the window; a chosen resolution is kept', () => {
    expect(resolutionFor('auto', '1h')).toBe('raw');
    expect(resolutionFor('auto', '6h')).toBe('1min');
    expect(resolutionFor('auto', '7d')).toBe('1hour');
    expect(resolutionFor('5min', '1h')).toBe('5min');
  });
  test("every window's own resolution draws it in one request", () => {
    for (const w of WINDOWS) expect(resolutionFits(w.resolution, w.seconds)).toBe(true);
  });
  test('a resolution with more buckets than one request returns does not fit', () => {
    expect(resolutionFits('1min', windowOf('7d').seconds)).toBe(false);
    expect(10080).toBeGreaterThan(POINTS_PER_REQUEST);
    expect(resolutionFits('raw', windowOf('7d').seconds)).toBe(true);
  });
  test('an unknown window reads as one hour', () => {
    expect(windowOf('nope').value).toBe('1h');
  });
});
