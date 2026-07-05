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
