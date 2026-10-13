// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

// Who may change tenant data (docs/operations/user-management.md). The gateway enforces it
// (observer gets 403); the UI only hides or disables what the user cannot do anyway.
export const WRITER_ROLES = ['engineer', 'admin'];

// The signed-in user's saved (non-sensitive) profile, as Login stores it.
export function currentUser() {
  try {
    const saved = localStorage.getItem('user');
    return saved ? JSON.parse(saved) : null;
  } catch {
    return null;
  }
}

export function canWrite(user = currentUser()) {
  return Boolean(user && (user.is_superuser || WRITER_ROLES.includes(user.role)));
}

export const READ_ONLY_HINT = 'Read-only: your role (observer) cannot change this';
