// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

// The reading line: one sentence under the page title that states what the live data shows.
// It says only what the data supports: how many sensors report, on which machines, and which
// of them sit in an open alert.

import { sensorLabel } from './sensors';

const WORDS = ['no', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine',
  'ten', 'eleven', 'twelve'];

export function countWord(n) {
  return n < WORDS.length ? WORDS[n] : String(n);
}

const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

// "a", "a and b", "a, b and c", "a, b, c and 2 more"
export function listNames(names, max = 3) {
  if (names.length <= 1) return names.join('');
  const shown = names.slice(0, max);
  const rest = names.length - shown.length;
  if (rest > 0) return `${shown.join(', ')} and ${rest} more`;
  return `${shown.slice(0, -1).join(', ')} and ${shown[shown.length - 1]}`;
}

const sensorName = (sensors, id) => sensorLabel(sensors[id], id);

export function coreReading({ sensors = {}, statusBySensor = {}, wsConnected = true }) {
  const ids = Object.keys(sensors);
  if (!ids.length) {
    return 'Waiting for the first reading. Sensors appear here as soon as a device reports.';
  }

  const machines = [...new Set(ids.map((id) => sensors[id].equipment_name || sensors[id].equipment_id))];
  const n = ids.length;
  const where = machines.length === 1 ? `on ${machines[0]}` : `on ${countWord(machines.length)} machines`;
  const parts = [`${cap(countWord(n))} ${n === 1 ? 'sensor' : 'sensors'} ${where} ${n === 1 ? 'is' : 'are'} reporting.`];

  const crit = ids.filter((id) => statusBySensor[id] === 'crit');
  const warn = ids.filter((id) => statusBySensor[id] === 'warn');
  const names = (list) => listNames(list.map((id) => sensorName(sensors, id)));
  if (crit.length) {
    parts.push(crit.length === 1
      ? `One sensor is in a critical alert: ${names(crit)}.`
      : `${cap(countWord(crit.length))} sensors are in critical alerts: ${names(crit)}.`);
  } else if (warn.length) {
    parts.push(warn.length === 1
      ? `One sensor needs a look: ${names(warn)}.`
      : `${cap(countWord(warn.length))} sensors need a look: ${names(warn)}.`);
  } else {
    parts.push('Nothing needs attention.');
  }

  if (!wsConnected) parts.push('The live stream is reconnecting; these are the last readings.');
  return parts.join(' ');
}

const SEVERITY_RANK = { critical: 0, high: 1, medium: 2, low: 3, info: 4 };

const clock = (ts) => new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

// Alerts: what is open, the most severe of it, and how quiet the last day was otherwise.
export function alertsReading({ alerts = [], now = Date.now() }) {
  const open = alerts.filter((a) => !a.acknowledged);
  const dayAgo = now - 24 * 3600 * 1000;
  const lastDay = alerts.filter((a) => new Date(a.triggered_at).getTime() >= dayAgo).length;

  if (!alerts.length) return 'No alerts have fired. Alert rules decide what raises one.';
  if (!open.length) {
    return lastDay
      ? `No alerts are open. ${cap(countWord(lastDay))} fired in the last 24 hours, all acknowledged.`
      : 'No alerts are open, and none fired in the last 24 hours.';
  }
  const worst = [...open].sort((a, b) =>
    (SEVERITY_RANK[a.severity] ?? 5) - (SEVERITY_RANK[b.severity] ?? 5)
    || new Date(a.triggered_at) - new Date(b.triggered_at))[0];
  const what = worst.message || worst.rule_name || 'an alert';
  const since = worst.triggered_at ? `, since ${clock(worst.triggered_at)}` : '';
  if (open.length === 1) return `One alert is open: ${what}${since}.`;
  return `${cap(countWord(open.length))} alerts are open. The most severe is ${worst.severity || 'unrated'}: ${what}${since}.`;
}

// Equipment: how many machines report, and through how many sensors.
export function equipmentReading({ equipment = [] }) {
  if (!equipment.length) return 'No machines yet. Add one to start receiving its readings.';
  const sensors = equipment.reduce((n, e) => n + (Number(e.sensor_count) || 0), 0);
  const machines = equipment.length === 1 ? 'One machine is' : `${cap(countWord(equipment.length))} machines are`;
  const tail = equipment.length === 1 ? '' : ' between them';
  return `${machines} configured, with ${countWord(sensors)} ${sensors === 1 ? 'sensor' : 'sensors'}${tail}.`;
}
