// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

// Pure metric-display helpers shared by the Models views (kept out of the component file so
// fast-refresh stays clean and they're unit-testable).

// Headline metric: the one number worth showing in the table. Prefer well-known keys, else the
// first metric the run logged — so every model still shows *something* instrument-like.
export const PRIMARY_METRIC_ORDER = ['f1', 'f1_score', 'accuracy', 'auc', 'auc_roc', 'r2', 'rmse', 'mae', 'mape'];

export function fmtMetric(v) {
  if (v === null || v === undefined || v === '') return '—';
  const n = Number(v);
  if (Number.isNaN(n)) return String(v);
  if (Number.isInteger(n)) return String(n);
  return Math.abs(n) >= 1000 ? n.toFixed(0) : n.toFixed(4).replace(/0+$/, '').replace(/\.$/, '');
}

export function pickPrimary(metrics) {
  const keys = Object.keys(metrics || {});
  if (!keys.length) return null;
  const lower = Object.fromEntries(keys.map((k) => [k.toLowerCase(), k]));
  for (const want of PRIMARY_METRIC_ORDER) {
    if (lower[want]) return { label: want.replace(/_/g, ' '), value: metrics[lower[want]] };
  }
  return { label: keys[0].replace(/_/g, ' '), value: metrics[keys[0]] };
}
