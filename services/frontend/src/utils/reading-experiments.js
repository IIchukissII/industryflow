// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

// Experiments: how many the tenant tracks, which one moved last and when, and how many registered
// models came out of a tracked run.

import { countWord } from './reading';
import { agoPhrase } from './experiments';

const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

// experiments: [{ name, last_update_time }]; registered: how many registered models name a run.
export function experimentsReading({ experiments = [], registered = 0, now = Date.now() }) {
  const n = experiments.length;
  if (!n) return 'No experiments are tracked yet. Runs logged from a notebook appear here.';
  const latest = [...experiments].sort((a, b) => (b.last_update_time || 0) - (a.last_update_time || 0))[0];
  const when = latest.last_update_time ? agoPhrase(latest.last_update_time, now) : '';
  const parts = [n === 1
    ? `One experiment is tracked: ${latest.name}${when ? `, changed ${when}` : ''}.`
    : `${cap(countWord(n))} experiments are tracked. ${latest.name} changed most recently${when ? `, ${when}` : ''}.`];
  if (registered) {
    parts.push(registered === 1
      ? 'One registered model came from a tracked run.'
      : `${cap(countWord(registered))} registered models came from tracked runs.`);
  }
  return parts.join(' ');
}
