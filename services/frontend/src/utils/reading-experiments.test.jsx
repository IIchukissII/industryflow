// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { experimentsReading } from './reading-experiments';

const now = Date.UTC(2026, 9, 25, 12, 0, 0);
const hoursAgo = (h) => now - h * 3600_000;

describe('experimentsReading', () => {
  it('says where experiments come from when there are none', () => {
    expect(experimentsReading({ experiments: [], now }))
      .toBe('No experiments are tracked yet. Runs logged from a notebook appear here.');
  });

  it('names the one experiment and when it changed', () => {
    expect(experimentsReading({ experiments: [{ name: 'pump-anomaly', last_update_time: hoursAgo(2) }], now }))
      .toBe('One experiment is tracked: pump-anomaly, changed 2 hours ago.');
  });

  it('counts experiments, names the most recent, and counts registered models from runs', () => {
    const experiments = [
      { name: 'old', last_update_time: hoursAgo(50) },
      { name: 'pump-anomaly', last_update_time: hoursAgo(3) },
      { name: 'mid', last_update_time: hoursAgo(10) },
    ];
    expect(experimentsReading({ experiments, registered: 2, now })).toBe(
      'Three experiments are tracked. pump-anomaly changed most recently, 3 hours ago. '
      + 'Two registered models came from tracked runs.',
    );
  });

  it('dates an older change', () => {
    expect(experimentsReading({ experiments: [{ name: 'a', last_update_time: Date.UTC(2026, 6, 16) }, { name: 'b' }], now }))
      .toBe('Two experiments are tracked. a changed most recently, on 2026-07-16.');
  });

  it('leaves out a time it does not have', () => {
    expect(experimentsReading({ experiments: [{ name: 'x' }], registered: 1, now }))
      .toBe('One experiment is tracked: x. One registered model came from a tracked run.');
  });
});
