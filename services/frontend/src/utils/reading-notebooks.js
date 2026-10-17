// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

// Notebooks: whether the embedded environment is up yet, and what it can reach.

export function notebooksReading({ loaded = false, slow = false }) {
  if (loaded) return 'Your notebook environment is open. It can read your tenant’s data and no other tenant’s.';
  if (slow) return 'Your environment is still starting. The first launch takes the longest.';
  return 'Your notebook environment is starting. It can read your tenant’s data and no other tenant’s.';
}
