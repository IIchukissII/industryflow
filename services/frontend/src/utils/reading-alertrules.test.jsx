// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { alertRulesReading } from './reading-alertrules';

const r = (detection_type, enabled = true) => ({ detection_type, enabled });

describe('alertRulesReading', () => {
  it('says nothing raises an alert without rules', () => {
    expect(alertRulesReading({ rules: [] })).toBe('No alert rules yet. Nothing raises an alert until a rule exists.');
  });

  it('names the one kind when every active rule is of it', () => {
    expect(alertRulesReading({ rules: [r('threshold'), r('threshold')] })).toBe('Two threshold rules are active.');
    expect(alertRulesReading({ rules: [r('ml')] })).toBe('One model rule is active.');
  });

  it('breaks several kinds down in a fixed order', () => {
    const rules = [r('statistical'), r('threshold'), r('ml'), r('threshold'), r('threshold')];
    expect(alertRulesReading({ rules })).toBe('Five rules are active: three threshold, one model and one drift.');
  });

  it('counts the rules that are switched off', () => {
    expect(alertRulesReading({ rules: [r('threshold'), r('ml', false)] }))
      .toBe('One threshold rule is active. One more is switched off.');
    expect(alertRulesReading({ rules: [r('threshold'), r('ml', false), r('ml', false)] }))
      .toBe('One threshold rule is active. Two more are switched off.');
  });

  it('says when no rule is active', () => {
    expect(alertRulesReading({ rules: [r('ml', false)] })).toBe('No rules are active. The one rule on record is switched off.');
    expect(alertRulesReading({ rules: [r('ml', false), r('threshold', false)] })).toBe('No rules are active. Both are switched off.');
    expect(alertRulesReading({ rules: [r('ml', false), r('ml', false), r('ml', false)] })).toBe('No rules are active. All three are switched off.');
  });
});
