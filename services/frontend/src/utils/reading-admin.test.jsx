// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { companiesReading, usersReading } from './reading-admin';

describe('companiesReading', () => {
  it('invites the first company', () => {
    expect(companiesReading({ companies: [] })).toBe('No companies yet. Add one to give its users a tenant.');
  });

  it('says when all are active', () => {
    const companies = [{ is_active: true }, { is_active: true }, { is_active: true }];
    expect(companiesReading({ companies })).toBe('Three companies have a tenant here, all active.');
  });

  it('counts the deactivated ones', () => {
    const companies = [{ is_active: true }, { is_active: false }];
    expect(companiesReading({ companies })).toBe('Two companies have a tenant here. One is deactivated.');
  });

  it('handles one company', () => {
    expect(companiesReading({ companies: [{ is_active: true }] })).toBe('One company has a tenant here, and it is active.');
  });
});

describe('usersReading', () => {
  const companies = [{ company_id: 'c1', company_name: 'Acme' }, { company_id: 'c2', company_name: 'Globex' }];
  const u = (role, company_id = 'c1', extra = {}) => ({ role, company_id, is_active: true, is_superuser: false, ...extra });

  it('invites the first user', () => {
    expect(usersReading({ users: [] })).toBe('No users yet. Add one with a company and a role.');
  });

  it('names the one company and counts roles', () => {
    const users = [u('admin'), u('engineer'), u('engineer'), u('observer')];
    expect(usersReading({ users, companies })).toBe('Four users in Acme: one admin, two engineers and one observer.');
  });

  it('counts companies, inactive users and superusers', () => {
    const users = [u('admin', 'c1', { is_superuser: true }), u('observer', 'c2', { is_active: false })];
    expect(usersReading({ users, companies })).toBe(
      'Two users across two companies: one admin and one observer. One is inactive. One is a superuser.',
    );
  });
});
