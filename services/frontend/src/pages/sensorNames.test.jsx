// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { describe, test, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import AlertHistory from './AlertHistory';
import AlertRules from './AlertRules';

// The pages show what a sensor, machine or model is called, not its id.

const SENSOR = '5f0e8a1c-2b3d-4e5f-8a9b-0c1d2e3f4a5b';

const RULES = [
  { rule_id: 'r1', name: 'Hot roots', detection_type: 'threshold', severity: 'high', enabled: true,
    condition: 'greater_than', threshold: 30, sensor_id: 'u1', sensor_name: 'temp_root_1', sensor_display_name: 'Root zone' },
  { rule_id: 'r2', name: 'Orphan', detection_type: 'threshold', severity: 'low', enabled: true,
    condition: 'less_than', threshold: 1, sensor_id: SENSOR, sensor_name: null, sensor_display_name: null },
];
const ALERT = {
  alert_id: 'a1', rule_id: 'r1', rule_name: 'Hot roots', severity: 'high', detection_type: 'threshold',
  message: 'too hot', triggered_at: '2026-10-13T10:00:00Z', acknowledged: false, label_verdict: null,
  sensor_id: 'u1', sensor_name: 'temp_root_1', sensor_display_name: 'Root zone', equipment_name: 'Cabinet',
};

function respond(body) {
  return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
}

vi.mock('../services/http', () => ({
  default: (path) => {
    if (path.startsWith('/api/alerts/label-metrics')) return respond({ overall: {}, series: [] });
    if (path.startsWith('/api/alerts')) return respond([ALERT]);
    if (path.startsWith('/api/alert-rules')) return respond(RULES);
    return respond([]);
  },
}));

describe('names instead of ids', () => {
  test('alert rules name their sensor, and say when it was deleted', async () => {
    render(<MemoryRouter><AlertRules /></MemoryRouter>);
    expect(await screen.findByText('sensor Root zone')).toBeInTheDocument();
    expect(screen.getByText('5f0e8a1c')).toHaveAttribute('title', SENSOR);
    expect(screen.getByText('a deleted sensor (')).toBeInTheDocument();
  });

  test('an alert row names its sensor by the display name', async () => {
    render(<MemoryRouter><AlertHistory /></MemoryRouter>);
    expect(await screen.findByText(/Cabinet, Root zone, Hot roots, high/)).toBeInTheDocument();
  });
});
