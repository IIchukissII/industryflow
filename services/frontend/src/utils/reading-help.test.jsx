// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { helpReading } from './reading-help';

describe('helpReading', () => {
  it('tells an observer it can look but not change', () => {
    expect(helpReading({ user: { role: 'observer' } })).toMatch(/^You are an observer: .*but not change it\.$/);
  });

  it('tells an engineer what it can change', () => {
    expect(helpReading({ user: { role: 'engineer' } })).toMatch(/^You are an engineer: you can change/);
  });

  it('tells an admin it manages users', () => {
    expect(helpReading({ user: { role: 'admin' } })).toMatch(/manage its users\.$/);
  });

  it('puts superuser before the role', () => {
    expect(helpReading({ user: { role: 'observer', is_superuser: true } })).toMatch(/^You are a superuser/);
  });

  it('falls back to what the guide covers without a known role', () => {
    expect(helpReading({})).toMatch(/^This guide covers/);
    expect(helpReading({ user: { role: 'other' } })).toMatch(/^This guide covers/);
  });
});
