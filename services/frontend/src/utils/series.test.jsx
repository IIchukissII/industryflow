// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { describe, test, expect } from 'vitest';
import { dedupeAscending } from './series';

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
