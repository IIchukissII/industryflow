// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { alertRulesReading, ruleSentence, ruleSentenceParts } from './reading-alertrules';

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

describe('ruleSentence', () => {
  const SENSOR = '5f0e8a1c-2b3d-4e5f-8a9b-0c1d2e3f4a5b';
  const MODEL = 'abcd1234-0000-4000-8000-000000000000';
  const base = { severity: 'high', enabled: true };

  it('names the sensor by its display name, else its key', () => {
    const rule = { ...base, detection_type: 'threshold', condition: 'greater_than', threshold: 30,
      sensor_id: SENSOR, sensor_name: 'temp_root_1', sensor_display_name: 'Root zone' };
    expect(ruleSentence(rule)).toBe('Threshold rule. Fires on a reading above 30 from sensor Root zone, at high severity.');
    expect(ruleSentence({ ...rule, sensor_display_name: null }))
      .toBe('Threshold rule. Fires on a reading above 30 from sensor temp_root_1, at high severity.');
  });

  it('says a sensor was deleted when the join finds nothing, keeping its short id', () => {
    const rule = { ...base, detection_type: 'threshold', condition: 'less_than', threshold: 1,
      sensor_id: SENSOR, sensor_name: null, sensor_display_name: null };
    expect(ruleSentence(rule)).toBe('Threshold rule. Fires on a reading below 1 from a deleted sensor (5f0e8a1c), at high severity.');
    const id = ruleSentenceParts(rule).find((p) => p.t === '5f0e8a1c');
    expect(id).toMatchObject({ mono: true, title: SENSOR });
  });

  it('names the machine and the model of a model rule', () => {
    const rule = { ...base, detection_type: 'ml', anomaly_threshold: 0.9, equipment_id: 'e1',
      equipment_name: 'Pump A', model_id: MODEL, model_name: 'pump-iforest', model_version: 3 };
    expect(ruleSentence(rule)).toBe(
      'Model rule. Model pump-iforest v3 scores readings from machine Pump A; the rule fires past an anomaly threshold of 0.9, at high severity.');
  });

  it('says a model or machine was deleted', () => {
    const rule = { ...base, detection_type: 'statistical', threshold: 0.3, equipment_id: 'e1-gone',
      equipment_name: null, model_id: MODEL, model_name: null };
    expect(ruleSentence(rule)).toBe(
      'Drift rule. Fires when a deleted model (abcd1234) passes a drifted-feature share of 0.3, for a deleted machine (e1-gone), at high severity.');
  });

  it('keeps patterns and the every-sensor case', () => {
    expect(ruleSentence({ ...base, detection_type: 'threshold', condition: 'equals', threshold: 0, sensor_pattern: 'temp_root_*' }))
      .toBe('Threshold rule. Fires on a reading equal to 0 from sensors matching temp_root_*, at high severity.');
    expect(ruleSentence({ ...base, detection_type: 'threshold', condition: 'between', threshold_min: 1, threshold_max: 2 }))
      .toBe('Threshold rule. Fires on a reading between 1 and 2 from every sensor, at high severity.');
  });
});
