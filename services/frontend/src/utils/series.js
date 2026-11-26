// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

// Sort chart points ascending by time and collapse duplicate timestamps (last write wins), so a
// re-sent reading updates its point rather than adding a spurious one. Pure + unit-testable.
export const dedupeAscending = (rows) => {
  rows.sort((a, b) => a.time - b.time);
  const out = [];
  for (const r of rows) {
    if (out.length && out[out.length - 1].time === r.time) out[out.length - 1] = r; // last wins per ts
    else out.push(r);
  }
  return out;
};

// The windows a chart shows, each ending now, and the resolution each draws by default: raw readings
// while a window is short, the stream's own aggregates (1 minute, 5 minutes, 1 hour) as it grows, so a
// window never asks for more points than one request returns.
export const WINDOWS = [
  { value: '15m', label: '15 min', seconds: 15 * 60, resolution: 'raw' },
  { value: '1h', label: '1 hour', seconds: 60 * 60, resolution: 'raw' },
  { value: '6h', label: '6 hours', seconds: 6 * 60 * 60, resolution: '1min' },
  { value: '24h', label: '24 hours', seconds: 24 * 60 * 60, resolution: '5min' },
  { value: '7d', label: '7 days', seconds: 7 * 24 * 60 * 60, resolution: '1hour' },
];

// The API's cap on rows per request (/api/measurements, /api/aggregations).
export const POINTS_PER_REQUEST = 1000;

const BUCKET_SECONDS = { '1min': 60, '5min': 300, '1hour': 3600 };

export const windowOf = (value) => WINDOWS.find((w) => w.value === value) || WINDOWS[1];

// Whether a resolution can draw a whole window in one request: raw is decided by the readings
// themselves (see the chart's fallback), an aggregate by its bucket count.
export const resolutionFits = (resolution, windowSeconds) =>
  resolution === 'raw' || windowSeconds / BUCKET_SECONDS[resolution] <= POINTS_PER_REQUEST;

// The resolution drawn: the window's own for 'auto', else the one chosen.
export const resolutionFor = (chosen, windowValue) =>
  chosen === 'auto' ? windowOf(windowValue).resolution : chosen;

// Raw readings as chart points. The chart's time axis counts whole seconds, so readings within one
// second become one point, the last of them; `merged` says how many readings were folded away.
export const toSecondPoints = (rows) => {
  const points = dedupeAscending(
    rows.map((r) => ({ time: Math.floor(new Date(r.time).getTime() / 1000), value: r.value, at: new Date(r.time).getTime() }))
      .sort((a, b) => a.at - b.at)
      .map(({ time, value }) => ({ time, value }))
  );
  return { points, merged: rows.length - points.length };
};
