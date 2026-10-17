// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { coreReading, listNames } from './reading';

const s = (name, eq = 'Strawberry cabinet') => ({ sensor_name: name, equipment_name: eq, value: 1 });

describe('coreReading', () => {
  it('invites the first reading when nothing reports', () => {
    expect(coreReading({ sensors: {} })).toMatch(/^Waiting for the first reading/);
  });

  it('names the one machine and says nothing needs attention', () => {
    const sensors = { a: s('ec'), b: s('ph') };
    expect(coreReading({ sensors })).toBe('Two sensors on Strawberry cabinet are reporting. Nothing needs attention.');
  });

  it('counts machines instead of naming several', () => {
    const sensors = { a: s('ec', 'A'), b: s('ph', 'B') };
    expect(coreReading({ sensors })).toMatch(/^Two sensors on two machines are reporting\./);
  });

  it('puts critical before warning and names the sensors', () => {
    const sensors = { a: s('ec'), b: s('ph'), c: s('vpd') };
    const r = coreReading({ sensors, statusBySensor: { a: 'warn', b: 'crit' } });
    expect(r).toMatch(/One sensor is in a critical alert: ph\.$/);
  });

  it('lists several sensors that need a look', () => {
    const sensors = { a: s('ec'), b: s('ph'), c: s('vpd') };
    const r = coreReading({ sensors, statusBySensor: { a: 'warn', c: 'warn' } });
    expect(r).toMatch(/Two sensors need a look: ec and vpd\.$/);
  });

  it('says when the stream is reconnecting', () => {
    const r = coreReading({ sensors: { a: s('ec') }, wsConnected: false });
    expect(r).toMatch(/reconnecting; these are the last readings\.$/);
  });
});

describe('listNames', () => {
  it('joins with commas and "and", and caps the list', () => {
    expect(listNames(['a'])).toBe('a');
    expect(listNames(['a', 'b', 'c'])).toBe('a, b and c');
    expect(listNames(['a', 'b', 'c', 'd', 'e'])).toBe('a, b, c and 2 more');
  });
});
