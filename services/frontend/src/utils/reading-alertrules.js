// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

// Alert rules: how many rules are active, of which kinds, and how many are switched off; and each
// rule as one sentence that names what it watches.

import { countWord, listNames } from './reading';
import { sensorLabel } from './sensors';

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

// --- One rule, as a sentence ---------------------------------------------------------------------
// A sentence is a list of segments: plain text, or a value set in mono (`mono`), optionally with
// the full value as a tooltip (`title`). ruleSentence() joins them as plain text.

const seg = (t) => ({ t });
const mono = (t, title) => ({ t: String(t), mono: true, ...(title ? { title } : {}) });
const shortId = (id) => { const s = String(id); return s.length > 12 ? s.slice(0, 8) : s; };

// A target the join found nothing for was deleted: say so, and keep its short id to tell them apart.
const deleted = (what, id) => [seg(`a deleted ${what} (`), mono(shortId(id), String(id)), seg(')')];

// What the rule watches, by name.
export function ruleTargetParts(rule) {
  if (rule.sensor_id) {
    const name = sensorLabel(rule, '');
    return name ? [seg(`sensor ${name}`)] : deleted('sensor', rule.sensor_id);
  }
  if (rule.equipment_id) {
    return rule.equipment_name ? [seg(`machine ${rule.equipment_name}`)] : deleted('machine', rule.equipment_id);
  }
  if (rule.sensor_pattern) return [seg('sensors matching '), mono(rule.sensor_pattern)];
  return [seg('every sensor')];
}

// The model a model or drift rule scores with, by name.
export function ruleModelParts(rule) {
  if (!rule.model_id) return [seg('no model')];
  if (!rule.model_name) return deleted('model', rule.model_id);
  return [seg(`model ${rule.model_name}${rule.model_version != null ? ` v${rule.model_version}` : ''}`)];
}

function conditionParts(rule) {
  switch (rule.condition) {
    case 'greater_than': return [seg('above '), mono(rule.threshold)];
    case 'less_than': return [seg('below '), mono(rule.threshold)];
    case 'equals': return [seg('equal to '), mono(rule.threshold)];
    case 'between': return [seg('between '), mono(rule.threshold_min), seg(' and '), mono(rule.threshold_max)];
    case 'outside_range': return [seg('outside '), mono(rule.threshold_min), seg(' to '), mono(rule.threshold_max)];
    default: return [seg('with no condition set')];
  }
}

const capFirst = ([first, ...rest]) => [{ ...first, t: cap(first.t) }, ...rest];

// What the rule watches and when it fires.
export function ruleSentenceParts(rule) {
  const target = ruleTargetParts(rule);
  const sev = seg(`, at ${rule.severity} severity.`);
  const model = ruleModelParts(rule);
  if (rule.detection_type === 'ml') {
    return [seg('Model rule. '), ...capFirst(model), seg(' scores readings from '), ...target,
      seg('; the rule fires past an anomaly threshold of '), mono(rule.anomaly_threshold ?? 0.85), sev];
  }
  if (rule.detection_type === 'statistical') {
    return [seg('Drift rule. Fires when '), ...model, seg(' passes a drifted-feature share of '),
      rule.threshold != null ? mono(rule.threshold) : seg('the service default'), seg(', for '), ...target, sev];
  }
  return [seg(`${cap(kindWord(rule.detection_type))} rule. Fires on a reading `), ...conditionParts(rule),
    seg(' from '), ...target, sev];
}

export const ruleSentence = (rule) => ruleSentenceParts(rule).map((p) => p.t).join('');
