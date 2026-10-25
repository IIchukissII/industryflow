// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

// Experiments: the pure shaping behind the experiments page. Runs come from the session-scoped
// browse path (/api/experiments, ADR-0019 dec 7), times in epoch milliseconds as MLflow keeps them.

// The metric columns a run table shows: the keys most runs carry, then by name, at most `max`;
// an opened run shows all of them.
export function metricColumns(runs, max = 3) {
  const seen = new Map();
  for (const r of runs) for (const k of Object.keys(r.metrics || {})) seen.set(k, (seen.get(k) || 0) + 1);
  return [...seen.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, max)
    .map(([k]) => k);
}

// "4 s", "3 min 05 s", "2 h 07 min"; null while a run has no end.
export function duration(run) {
  if (run.start_time == null || run.end_time == null) return null;
  const s = Math.max(0, Math.round((run.end_time - run.start_time) / 1000));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ${String(s % 60).padStart(2, '0')} s`;
  return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')} min`;
}

// "just now", "5 minutes ago", "3 hours ago", "2 days ago", then the date.
export function ago(ms, now = Date.now()) {
  if (!ms) return '';
  const s = Math.max(0, Math.round((now - ms) / 1000));
  const unit = (n, w) => `${n} ${w}${n === 1 ? '' : 's'} ago`;
  if (s < 60) return 'just now';
  if (s < 3600) return unit(Math.floor(s / 60), 'minute');
  if (s < 86400) return unit(Math.floor(s / 3600), 'hour');
  if (s < 7 * 86400) return unit(Math.floor(s / 86400), 'day');
  return new Date(ms).toISOString().slice(0, 10);
}

// ago() inside a sentence: "2 hours ago", or "on 2026-07-16" once it is a date.
export function agoPhrase(ms, now = Date.now()) {
  const a = ago(ms, now);
  return /^\d{4}-/.test(a) ? `on ${a}` : a;
}

// MLflow run statuses, as the status dot shows them.
const STATUS = {
  RUNNING: { label: 'Running', tone: 'live' },
  SCHEDULED: { label: 'Scheduled', tone: 'pending' },
  FINISHED: { label: 'Finished', tone: 'done' },
  FAILED: { label: 'Failed', tone: 'crit' },
  KILLED: { label: 'Stopped', tone: 'warn' },
};
export function runStatus(status) {
  return STATUS[status] || { label: status ? status.toLowerCase() : 'unknown', tone: 'pending' };
}

// Runs ordered for the table: by a metric (runs without it last, whichever the direction), or
// newest first.
export function sortRuns(runs, sort) {
  const out = [...runs];
  if (!sort || sort.key === 'start_time') {
    const dir = sort?.dir === 'asc' ? 1 : -1;
    return out.sort((a, b) => dir * ((a.start_time || 0) - (b.start_time || 0)));
  }
  const dir = sort.dir === 'asc' ? 1 : -1;
  return out.sort((a, b) => {
    const x = a.metrics?.[sort.key];
    const y = b.metrics?.[sort.key];
    if (x === undefined && y === undefined) return 0;
    if (x === undefined) return 1;
    if (y === undefined) return -1;
    return dir * (x - y);
  });
}

// run_id -> the registered model name its latest version came from.
export function registeredByRun(models) {
  const out = {};
  for (const m of models || []) if (m.run_id) out[m.run_id] = m.name;
  return out;
}

// A metric value as the table shows it: four significant digits, no exponent below 1e6.
export function formatMetric(v) {
  if (v === undefined || v === null || Number.isNaN(v)) return '';
  if (Number.isInteger(v)) return String(v);
  const a = Math.abs(v);
  if (a !== 0 && (a < 1e-3 || a >= 1e6)) return v.toExponential(3);
  return String(Number(v.toPrecision(4)));
}
