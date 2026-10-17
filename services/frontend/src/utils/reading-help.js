// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

// Help: what the signed-in user's role lets them do, so the guide starts from their side of it.
// Roles as docs/operations/user-management.md defines them.

export function helpReading({ user } = {}) {
  if (user?.is_superuser) {
    return 'You are a superuser: you can manage every organization and its users.';
  }
  switch (user?.role) {
    case 'admin':
      return 'You are an admin: you can change what your organization monitors and manage its users.';
    case 'engineer':
      return 'You are an engineer: you can change equipment, alert rules and models, and handle alerts.';
    case 'observer':
      return 'You are an observer: you can see everything your organization monitors, but not change it.';
    default:
      return 'This guide covers watching your equipment, handling alerts, and building models.';
  }
}
