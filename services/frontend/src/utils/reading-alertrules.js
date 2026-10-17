// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

// Alert rules: how many rules are active, of which kinds, and how many are switched off.

import { countWord, listNames } from './reading';

const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

// The rule kinds in the order the page lists them, named as the user reads them.
export const RULE_KINDS = [
  { type: 'threshold', word: 'threshold' },
  { type: 'ml', word: 'model' },
  { type: 'statistical', word: 'drift' },
];

export const kindWord = (type) => RULE_KINDS.find((k) => k.type === type)?.word || type;

export function alertRulesReading({ rules = [] }) {
  if (!rules.length) return 'No alert rules yet. Nothing raises an alert until a rule exists.';

  const active = rules.filter((r) => r.enabled);
  const off = rules.length - active.length;
  if (!active.length) {
    if (off === 1) return 'No rules are active. The one rule on record is switched off.';
    if (off === 2) return 'No rules are active. Both are switched off.';
    return `No rules are active. All ${countWord(off)} are switched off.`;
  }

  const counts = new Map();
  for (const r of active) counts.set(r.detection_type, (counts.get(r.detection_type) || 0) + 1);
  const known = RULE_KINDS.map((k) => k.type);
  const kinds = [...known, ...[...counts.keys()].filter((t) => !known.includes(t))].filter((t) => counts.has(t));

  const n = active.length;
  const noun = n === 1 ? 'rule' : 'rules';
  const verb = n === 1 ? 'is' : 'are';
  let first;
  if (kinds.length === 1) {
    first = `${cap(countWord(n))} ${kindWord(kinds[0])} ${noun} ${verb} active.`;
  } else {
    const parts = kinds.map((t) => `${countWord(counts.get(t))} ${kindWord(t)}`);
    first = `${cap(countWord(n))} ${noun} ${verb} active: ${listNames(parts, parts.length)}.`;
  }
  if (!off) return first;
  return `${first} ${off === 1 ? 'One more is' : `${cap(countWord(off))} more are`} switched off.`;
}
