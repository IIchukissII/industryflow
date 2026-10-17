// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { sensorLabel } from './sensors';

describe('sensorLabel', () => {
  it('reads the display name first, then the key, then the id', () => {
    expect(sensorLabel({ display_name: 'Bus voltage', sensor_name: 'bus_voltage', sensor_id: 'u1' })).toBe('Bus voltage');
    expect(sensorLabel({ display_name: null, sensor_name: 'bus_voltage', sensor_id: 'u1' })).toBe('bus_voltage');
    expect(sensorLabel({ display_name: '', sensor_name: '', sensor_id: 'u1' })).toBe('u1');
  });

  it('reads the label an alert or a rule carries for its sensor', () => {
    expect(sensorLabel({ sensor_display_name: 'Light (PPFD)', sensor_name: 'ppfd' })).toBe('Light (PPFD)');
  });

  it('uses the fallback in place of the id', () => {
    expect(sensorLabel({ value: 1 }, 'key-from-map')).toBe('key-from-map');
    expect(sensorLabel({ sensor_id: 'u1' }, '')).toBe('');
    expect(sensorLabel(undefined, '5f0e8a1c…')).toBe('5f0e8a1c…');
    expect(sensorLabel(null)).toBe('');
  });
});
