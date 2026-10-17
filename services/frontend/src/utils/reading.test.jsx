// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { coreReading, listNames, alertsReading, equipmentReading } from './reading';

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

  it('names sensors by their display name, and by the key where there is none', () => {
    const sensors = {
      a: { ...s('bus_voltage'), display_name: 'Bus voltage' },
      b: { ...s('co2'), display_name: null },
      c: s('ppfd'),
    };
    const r = coreReading({ sensors, statusBySensor: { a: 'warn', b: 'warn' } });
    expect(r).toMatch(/Two sensors need a look: Bus voltage and co2\.$/);
  });

  it('falls back to the sensor id when a sensor has no name at all', () => {
    const r = coreReading({ sensors: { '5f0e8a1c': { equipment_name: 'Pump', value: 1 } }, statusBySensor: { '5f0e8a1c': 'crit' } });
    expect(r).toMatch(/One sensor is in a critical alert: 5f0e8a1c\.$/);
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

describe('alertsReading', () => {
  const now = Date.parse('2026-10-17T20:00:00Z');
  const at = (h) => new Date(now - h * 3600 * 1000).toISOString();

  it('says when nothing has ever fired', () => {
    expect(alertsReading({ alerts: [], now })).toMatch(/^No alerts have fired\./);
  });

  it('counts the last day when nothing is open', () => {
    const alerts = [{ acknowledged: true, triggered_at: at(2) }, { acknowledged: true, triggered_at: at(30) }];
    expect(alertsReading({ alerts, now })).toBe('No alerts are open. One fired in the last 24 hours, all acknowledged.');
  });

  it('names the one open alert', () => {
    const alerts = [{ acknowledged: false, severity: 'high', message: 'VPD above 1.30 kPa', triggered_at: at(1) }];
    expect(alertsReading({ alerts, now })).toMatch(/^One alert is open: VPD above 1\.30 kPa, since /);
  });

  it('leads with the most severe of several', () => {
    const alerts = [
      { acknowledged: false, severity: 'high', message: 'warm', triggered_at: at(1) },
      { acknowledged: false, severity: 'critical', message: 'pH low', triggered_at: at(2) },
    ];
    expect(alertsReading({ alerts, now })).toMatch(/^Two alerts are open\. The most severe is critical: pH low, since /);
  });
});

describe('equipmentReading', () => {
  it('invites the first machine', () => {
    expect(equipmentReading({ equipment: [] })).toMatch(/^No machines yet\./);
  });
  it('counts one machine and its sensors', () => {
    expect(equipmentReading({ equipment: [{ sensor_count: 11 }] })).toBe('One machine is configured, with eleven sensors.');
  });
  it('sums sensors across machines', () => {
    expect(equipmentReading({ equipment: [{ sensor_count: 4 }, { sensor_count: 3 }] }))
      .toBe('Two machines are configured, with seven sensors between them.');
  });
});
