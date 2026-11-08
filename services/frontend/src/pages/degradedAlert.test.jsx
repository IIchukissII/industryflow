// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { describe, test, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import AlertHistory from './AlertHistory';

// ADR-0044 dec 3: a degraded alert says so from its data, not its message; labels on degraded
// alerts are counted apart from precision (ADR-0022 dec 3).

const BASE = { rule_id: 'r1', rule_name: 'Model', severity: 'high', detection_type: 'ml', anomaly_score: 0.9,
               triggered_at: new Date().toISOString(), acknowledged: false, label_verdict: null };
const ALERTS = [
  { ...BASE, alert_id: 'd', message: 'ML anomaly detected', degraded: true, neutralized_features: ['temp_dev'] },
  { ...BASE, alert_id: 'o', message: 'ML anomaly ordinary', degraded: false, neutralized_features: [] },
  { ...BASE, alert_id: 'u', message: 'ML anomaly before', degraded: null, neutralized_features: null },
];

vi.mock('../services/http', () => ({
  default: (path) => Promise.resolve({ ok: true, json: () => Promise.resolve(
    path.startsWith('/api/alerts/label-metrics')
      ? { overall: { precision: 0.5, labeled_total: 4 }, degraded: { labeled_total: 2 }, series: [] }
      : path.startsWith('/api/alerts') ? ALERTS : []) }),
}));

afterEach(cleanup);

describe('a degraded alert', () => {
  test('names what it was scored without, and only that alert does', async () => {
    localStorage.setItem('user', JSON.stringify({ role: 'engineer' }));
    render(<MemoryRouter><AlertHistory /></MemoryRouter>);
    const lines = await screen.findAllByText(/Degraded score/);
    expect(lines).toHaveLength(1);
    expect(lines[0].textContent).toContain('temp_dev');
  });

  test('its labels are counted apart in the precision summary', async () => {
    localStorage.setItem('user', JSON.stringify({ role: 'engineer' }));
    render(<MemoryRouter><AlertHistory /></MemoryRouter>);
    expect(await screen.findByText(/2 on degraded alerts counted apart/)).toBeTruthy();
  });
});
