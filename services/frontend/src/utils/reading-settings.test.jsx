// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { settingsReading } from './reading-settings';

describe('settingsReading', () => {
  it('names the email and the role', () => {
    expect(settingsReading({ user: { email: 'ana@plant.io', role: 'engineer' } }))
      .toBe('You are signed in as ana@plant.io, an engineer.');
    expect(settingsReading({ user: { email: 'ana@plant.io', role: 'observer' } }))
      .toBe('You are signed in as ana@plant.io, an observer.');
    expect(settingsReading({ user: { email: 'ana@plant.io', role: 'admin' } }))
      .toBe('You are signed in as ana@plant.io, an admin.');
  });

  it('says superuser over the role', () => {
    expect(settingsReading({ user: { email: 'root@plant.io', role: 'admin', is_superuser: true } }))
      .toBe('You are signed in as root@plant.io, a superuser.');
  });

  it('leaves the role out when there is none', () => {
    expect(settingsReading({ user: { email: 'ana@plant.io' } })).toBe('You are signed in as ana@plant.io.');
  });

  it('says what to do when the profile is missing', () => {
    expect(settingsReading({ user: null })).toMatch(/Sign out and sign in again\.$/);
  });
});
