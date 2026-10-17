// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

// Settings: who you are signed in as, and with what role.

const article = (word) => (/^[aeiou]/i.test(word) ? 'an' : 'a');

export function settingsReading({ user } = {}) {
  if (!user?.email) return 'Your account details could not be read. Sign out and sign in again.';
  const role = user.is_superuser ? 'superuser' : user.role;
  if (!role) return `You are signed in as ${user.email}.`;
  return `You are signed in as ${user.email}, ${article(role)} ${role}.`;
}
