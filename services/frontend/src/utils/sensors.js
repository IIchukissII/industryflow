// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

// What a person reads for a sensor: its display name, else its key (sensor_name), else its id.
// The key is what feature configs, models and rule patterns reference, so it is never replaced,
// only labelled. An alert or a rule carries its sensor's label as sensor_display_name.
// `fallback`, when given, replaces the id: for a sensor object keyed by id that does not carry it,
// or where a full id would be noise.
export function sensorLabel(s, fallback) {
  const named = s?.display_name || s?.sensor_display_name || s?.sensor_name;
  if (named) return named;
  return fallback !== undefined ? fallback : (s?.sensor_id || '');
}
