// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import MLModels from './MLModels';

// A notebook-registered version becomes a platform model by promotion (ADR-0040), and a platform
// model says what its drift baseline lets monitoring do (ADR-0035 dec 4).

const NOTEBOOK = { name: 'vpd-regressor', latest_version: '3', metrics: {}, last_updated_timestamp: 2 };
const PLATFORM = {
  model_id: 'm1', model_name: 'bearing-wear', model_version: '1', status: 'active', created_at: '2026-10-01T00:00:00Z',
  baseline: { data_drift: false, prediction: null, reason: 'the sample carries prediction scores' },
};

let promotions;

function respond(body, status = 200) {
  return Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });
}

vi.mock('../services/http', () => ({
  default: (path, init = {}) => {
    if (path === '/api/registered-models') return respond({ total: 1, models: [NOTEBOOK] });
    if (path === '/api/models') return respond({ total: 1, models: [PLATFORM] });
    if (path.startsWith('/api/alerts')) return respond([]);
    if (path === '/api/registered-models/vpd-regressor') {
      return respond({ name: 'vpd-regressor', versions: [{ version: '3', current_stage: 'None', status: 'READY' }] });
    }
    if (path === '/api/feature-configs') return respond({ configs: [{ id: 'fc-1', name: 'climate', base_sensors: ['temp'] }] });
    if (path === '/api/equipment') return respond([{ equipment_id: 'eq-1', name: 'Cabinet 1' }]);
    if (path === '/api/ml/capabilities') return respond({ detectors: [{ name: 'direct', semantics: 'direct_score' }] });
    if (path.endsWith('/versions/3/promote') && init.method === 'POST') {
      promotions.push(JSON.parse(init.body));
      return respond({ model_id: 'm2', baseline: { data_drift: true, prediction: null, reason: 'derived when deployed' } }, 201);
    }
    return respond({}, 404);
  },
}));

vi.mock('../utils/roles', async (orig) => ({ ...(await orig()), canWrite: () => true }));

beforeEach(() => { promotions = []; });

function page() {
  return render(<MemoryRouter><MLModels /></MemoryRouter>);
}

describe('models page', () => {
  test('a notebook version is promoted with the inputs and detector the user chose', async () => {
    page();
    fireEvent.click(await screen.findByText('vpd-regressor'));
    fireEvent.click(await screen.findByRole('button', { name: 'Promote' }));
    fireEvent.change(await screen.findByLabelText('Feature config'), { target: { value: 'fc-1' } });
    fireEvent.change(screen.getByLabelText('Equipment'), { target: { value: 'eq-1' } });
    fireEvent.change(screen.getByLabelText('Detector'), { target: { value: 'direct' } });
    fireEvent.click(screen.getByRole('button', { name: 'Promote v3' }));
    await waitFor(() => expect(promotions).toHaveLength(1));
    expect(promotions[0]).toEqual({ feature_config_id: 'fc-1', equipment_id: 'eq-1', detector: 'direct' });
    expect(await screen.findByRole('status')).toHaveTextContent(/v3 is a platform model now.*Data drift only/);
  });

  test('promotion waits for a feature config', async () => {
    page();
    fireEvent.click(await screen.findByText('vpd-regressor'));
    fireEvent.click(await screen.findByRole('button', { name: 'Promote' }));
    expect(await screen.findByRole('button', { name: 'Promote v3' })).toBeDisabled();
  });

  test('a platform model without a baseline says it is not monitored, and why', async () => {
    page();
    fireEvent.click(await screen.findByText('bearing-wear'));
    expect(await screen.findByText('Not monitored for drift')).toBeInTheDocument();
    expect(screen.getByText('the sample carries prediction scores')).toBeInTheDocument();
  });
});
