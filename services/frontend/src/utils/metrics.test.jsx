// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { describe, test, expect } from 'vitest';
import { fmtMetric, pickPrimary } from './metrics';

describe('fmtMetric', () => {
  test('blank-ish values render as a dash', () => {
    expect(fmtMetric(null)).toBe('—');
    expect(fmtMetric(undefined)).toBe('—');
    expect(fmtMetric('')).toBe('—');
  });
  test('non-numeric passes through as a string', () => {
    expect(fmtMetric('n/a')).toBe('n/a');
  });
  test('integers render without decimals', () => {
    expect(fmtMetric(5)).toBe('5');
  });
  test('fractions strip trailing zeros (4 dp)', () => {
    expect(fmtMetric(0.75)).toBe('0.75');
    expect(fmtMetric(0.123456)).toBe('0.1235');
  });
  test('large magnitudes round to whole numbers', () => {
    expect(fmtMetric(1234.5)).toBe('1235');
  });
});

describe('pickPrimary', () => {
  test('empty / missing metrics → null', () => {
    expect(pickPrimary({})).toBeNull();
    expect(pickPrimary(null)).toBeNull();
  });
  test('prefers the earliest well-known metric in priority order', () => {
    expect(pickPrimary({ accuracy: 0.9, f1: 0.8 })).toEqual({ label: 'f1', value: 0.8 });
    expect(pickPrimary({ accuracy: 0.9, misc: 1 })).toEqual({ label: 'accuracy', value: 0.9 });
  });
  test('falls back to the first key (underscores → spaces) when none are well-known', () => {
    expect(pickPrimary({ weird_metric: 5 })).toEqual({ label: 'weird metric', value: 5 });
  });
});
