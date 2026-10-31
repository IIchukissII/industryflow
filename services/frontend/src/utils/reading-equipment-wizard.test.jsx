// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { describe, expect, it } from 'vitest';
import { wizardReading } from './reading-equipment-wizard';

describe('wizardReading', () => {
  it('asks for the machine on a new one, and describes it when editing', () => {
    expect(wizardReading({ step: 1 }).lede).toBe('Name the machine, and say how many sensors report from it.');
    expect(wizardReading({ step: 1, editing: true, name: 'Feed pump 7' }).lede)
      .toBe('What Feed pump 7 is, and where it stands.');
  });

  it('counts the sensor in hand and the ones done', () => {
    expect(wizardReading({ step: 2, name: 'Feed pump 7', position: 0, total: 4, configured: 0 }))
      .toEqual({ lede: 'Sensor 1 of 4 on Feed pump 7.', sub: 'None is set up yet.' });
    expect(wizardReading({ step: 2, name: 'Feed pump 7', position: 2, total: 4, configured: 2 }).sub)
      .toBe('Two of four set up.');
  });

  it('says what is missing before saving, and nothing when complete', () => {
    expect(wizardReading({ step: 3, name: 'Feed pump 7', total: 4, configured: 4 }))
      .toEqual({ lede: 'All four sensors of Feed pump 7 are set up.', pending: false });
    expect(wizardReading({ step: 3, name: 'Feed pump 7', total: 4, configured: 3 }))
      .toEqual({ lede: 'Three of four sensors on Feed pump 7 are set up.', sub: 'Set up the last one to save.', pending: true });
    expect(wizardReading({ step: 3, name: 'Feed pump 7', total: 4, configured: 1 }))
      .toEqual({ lede: 'One of four sensors on Feed pump 7 is set up.', sub: 'Set up the remaining three to save.', pending: true });
  });

  it('never prints an empty name', () => {
    expect(wizardReading({ step: 2, name: '  ', position: 0, total: 1 }).lede).toBe('Sensor 1 of 1 on this machine.');
  });
});
