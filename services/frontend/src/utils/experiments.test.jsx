// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import {
  ago, agoPhrase, duration, formatMetric, metricColumns, registeredByRun, runStatus, sortRuns,
} from './experiments';

const run = (id, start, metrics = {}, end = null) => ({ run_id: id, start_time: start, end_time: end, metrics, params: {} });

describe('metricColumns', () => {
  it('takes the keys most runs carry, then by name, at most three', () => {
    const runs = [
      run('a', 1, { f1: 1, auc: 1, loss: 1, zeta: 1, rare: 1 }),
      run('b', 2, { f1: 1, auc: 1, loss: 1, zeta: 1 }),
      run('c', 3, { f1: 1, auc: 1, beta: 1 }),
    ];
    expect(metricColumns(runs)).toEqual(['auc', 'f1', 'loss']);
    expect(metricColumns(runs, 4)).toEqual(['auc', 'f1', 'loss', 'zeta']);
  });

  it('is empty when no run logged a metric', () => {
    expect(metricColumns([run('a', 1)])).toEqual([]);
  });
});

describe('duration', () => {
  it('reads seconds, minutes and hours', () => {
    expect(duration(run('a', 0, {}, 4200))).toBe('4 s');
    expect(duration(run('a', 0, {}, 185_000))).toBe('3 min 05 s');
    expect(duration(run('a', 0, {}, 7_620_000))).toBe('2 h 07 min');
  });

  it('has none while a run has not ended', () => {
    expect(duration(run('a', 1000))).toBeNull();
  });
});

describe('ago', () => {
  const now = Date.UTC(2026, 9, 25, 12, 0, 0);
  it('reads the elapsed time in words, then the date', () => {
    expect(ago(now - 20_000, now)).toBe('just now');
    expect(ago(now - 60_000, now)).toBe('1 minute ago');
    expect(ago(now - 3 * 3600_000, now)).toBe('3 hours ago');
    expect(ago(now - 2 * 86400_000, now)).toBe('2 days ago');
    expect(ago(Date.UTC(2026, 8, 1), now)).toBe('2026-09-01');
  });
});

describe('agoPhrase', () => {
  const now = Date.UTC(2026, 9, 25, 12, 0, 0);
  it('puts "on" before a date and nothing before elapsed time', () => {
    expect(agoPhrase(now - 3 * 3600_000, now)).toBe('3 hours ago');
    expect(agoPhrase(Date.UTC(2026, 6, 16), now)).toBe('on 2026-07-16');
  });
});

describe('runStatus', () => {
  it('names each MLflow status and keeps an unknown one readable', () => {
    expect(runStatus('FAILED')).toEqual({ label: 'Failed', tone: 'crit' });
    expect(runStatus('KILLED').label).toBe('Stopped');
    expect(runStatus('WEIRD')).toEqual({ label: 'weird', tone: 'pending' });
  });
});

describe('sortRuns', () => {
  const runs = [run('a', 1, { auc: 0.7 }), run('b', 3), run('c', 2, { auc: 0.9 })];

  it('puts the newest first by default', () => {
    expect(sortRuns(runs).map((r) => r.run_id)).toEqual(['b', 'c', 'a']);
  });

  it('sorts by a metric and keeps runs without it last in either direction', () => {
    expect(sortRuns(runs, { key: 'auc', dir: 'desc' }).map((r) => r.run_id)).toEqual(['c', 'a', 'b']);
    expect(sortRuns(runs, { key: 'auc', dir: 'asc' }).map((r) => r.run_id)).toEqual(['a', 'c', 'b']);
  });

  it('does not reorder its input', () => {
    sortRuns(runs, { key: 'auc', dir: 'asc' });
    expect(runs.map((r) => r.run_id)).toEqual(['a', 'b', 'c']);
  });
});

describe('registeredByRun', () => {
  it('maps each run to the registered model its latest version came from', () => {
    expect(registeredByRun([{ name: 'pump', run_id: 'r1' }, { name: 'ext', run_id: null }])).toEqual({ r1: 'pump' });
  });
});

describe('formatMetric', () => {
  it('keeps four significant digits and integers as they are', () => {
    expect(formatMetric(0.912345)).toBe('0.9123');
    expect(formatMetric(42)).toBe('42');
    expect(formatMetric(1234.5678)).toBe('1235');
    expect(formatMetric(0.00001234)).toBe('1.234e-5');
    expect(formatMetric(undefined)).toBe('');
  });
});
