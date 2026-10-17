// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

// Administration: how many companies and users there are, and which of them are switched off.

import { countWord, listNames } from './reading';

const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
const counted = (n, one, many) => `${countWord(n)} ${n === 1 ? one : many}`;

export function companiesReading({ companies = [] }) {
  const n = companies.length;
  if (!n) return 'No companies yet. Add one to give its users a tenant.';
  const inactive = companies.filter((c) => !c.is_active).length;
  const head = cap(counted(n, 'company has', 'companies have')) + ' a tenant here';
  if (!inactive) return n === 1 ? `${head}, and it is active.` : `${head}, all active.`;
  if (inactive === n) return n === 1 ? `${head}, and it is deactivated.` : `${head}, all deactivated.`;
  return `${head}. ${cap(countWord(inactive))} ${inactive === 1 ? 'is' : 'are'} deactivated.`;
}

const ROLES = [['admin', 'admin', 'admins'], ['engineer', 'engineer', 'engineers'], ['observer', 'observer', 'observers']];

export function usersReading({ users = [], companies = [] }) {
  const n = users.length;
  if (!n) return 'No users yet. Add one with a company and a role.';

  const tenantIds = [...new Set(users.map((u) => u.company_id).filter(Boolean))];
  let where = '';
  if (tenantIds.length === 1) {
    const name = companies.find((c) => c.company_id === tenantIds[0])?.company_name;
    where = name ? ` in ${name}` : ' in one company';
  } else if (tenantIds.length > 1) {
    where = ` across ${countWord(tenantIds.length)} companies`;
  }
  const roles = ROLES
    .map(([r, one, many]) => counted(users.filter((u) => u.role === r).length, one, many))
    .filter((s) => !s.startsWith('no '));
  const parts = [`${cap(counted(n, 'user', 'users'))}${where}${roles.length ? `: ${listNames(roles)}` : ''}.`];

  const inactive = users.filter((u) => !u.is_active).length;
  if (inactive) parts.push(`${cap(countWord(inactive))} ${inactive === 1 ? 'is' : 'are'} inactive.`);
  const supers = users.filter((u) => u.is_superuser).length;
  if (supers) parts.push(supers === 1 ? 'One is a superuser.' : `${cap(countWord(supers))} are superusers.`);
  return parts.join(' ');
}
