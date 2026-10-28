// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import ModelUploadWizard from './ModelUploadWizard';

// The registration the wizard sends must carry what ml_service needs to score the model: its
// author's description and the feature config that computes its inputs. Without the config the
// model registers but can never be scored.

const CAPS = { detectors: [{ name: 'proba', semantics: 'anomaly_probability' }] };
const CONFIG = {
  id: 'fc-1', company_id: 'c1', equipment_type: 'pump', name: 'pump-vibration', description: null,
  base_sensors: ['vib_x', 'vib_y'], transformations: [{ type: 'rolling' }],
  created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z', created_by: null,
};

let featureConfigs;
let registrations;

function respond(body, status = 200) {
  return Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });
}

vi.mock('../services/http', () => ({
  default: (path, init = {}) => {
    if (path === '/api/ml/capabilities') return respond(CAPS);
    if (path === '/api/feature-configs') return respond({ total: featureConfigs.length, configs: featureConfigs });
    if (path === '/api/equipment') return respond([{ equipment_id: 'eq-1', name: 'Pump 7' }]);
    if (path === '/api/models/upload-capability') return respond({ capability: 'cap-token' }, 201);
    if (path === '/api/models' && init.method === 'POST') {
      registrations.push(JSON.parse(init.body));
      return respond({ status: 'success', model_id: 'm1', model_name: 'm' }, 201);
    }
    return respond({}, 404);
  },
}));

beforeEach(() => {
  featureConfigs = [CONFIG];
  registrations = [];
  // The gateway and the store: same-origin, as the wizard requires.
  vi.stubGlobal('fetch', vi.fn((url) => {
    if (url.endsWith('/stage')) return respond({ upload_id: 'u1', urls: { MLmodel: '/store/MLmodel' } });
    if (url.endsWith('/commit')) return respond({ artifact_uri: 's3://models/u1' });
    return respond({});
  }));
});

afterEach(() => vi.unstubAllGlobals());

async function declare({ description = 'Flags bearing wear from vibration.' } = {}) {
  render(<ModelUploadWizard onClose={() => {}} />);
  const input = document.querySelector('input[type="file"]');
  fireEvent.change(input, { target: { files: [new File(['flavors: {}'], 'MLmodel')] } });
  fireEvent.change(screen.getByPlaceholderText('yield-forecaster-xl'), { target: { value: 'bearing-wear' } });
  fireEvent.change(screen.getByPlaceholderText('gradient_boosting'), { target: { value: 'isolation_forest' } });
  if (description) {
    fireEvent.change(screen.getByLabelText(/Description/), { target: { value: description } });
  }
  fireEvent.click(await screen.findByText('anomaly probability'));
}

async function register() {
  fireEvent.click(screen.getByRole('button', { name: /Upload & register/ }));
  await waitFor(() => expect(registrations).toHaveLength(1));
  return registrations[0];
}

describe('model upload wizard registration', () => {
  test('sends the description and the chosen feature config', async () => {
    await declare();
    fireEvent.click(await screen.findByRole('button', { name: /pump-vibration/ }));
    const body = await register();
    expect(body.description).toBe('Flags bearing wear from vibration.');
    expect(body.feature_config_id).toBe('fc-1');
    expect(body.artifact_uri).toBe('s3://models/u1');
    expect(body.score_semantics).toBe('anomaly_probability');
  });

  test('a description is required before it can register', async () => {
    await declare({ description: '' });
    expect(screen.getByRole('button', { name: /Upload & register/ })).toBeDisabled();
  });

  test('with no feature config it says so, and still registers without one', async () => {
    featureConfigs = [];
    await declare();
    expect(await screen.findByText(/This tenant has no feature config yet/)).toBeInTheDocument();
    expect(screen.getByText(/can't be scored without one/)).toBeInTheDocument();
    const body = await register();
    expect(body.description).toBe('Flags bearing wear from vibration.');
    expect(body.feature_config_id).toBeNull();
    expect(await screen.findByText(/It has no feature config, so it can't be scored/)).toBeInTheDocument();
  });

  test('sends the feature sample and the equipment drift reads from', async () => {
    await declare();
    fireEvent.click(await screen.findByRole('button', { name: /pump-vibration/ }));
    fireEvent.change(await screen.findByLabelText('Equipment'), { target: { value: 'eq-1' } });
    const sample = { schema_version: 2, columns: ['vib_x', 'vib_y'], sample: { vib_x: [1], vib_y: [2] } };
    fireEvent.change(screen.getByLabelText('Feature sample'), {
      target: { files: [new File([JSON.stringify(sample)], 'feature_sample.json', { type: 'application/json' })] },
    });
    await waitFor(() => expect(screen.getByRole('button', { name: /Upload & register/ })).toBeEnabled());
    const body = await register();
    expect(body.equipment_id).toBe('eq-1');
    expect(body.reference_profile).toEqual(sample);
  });

  test('a sample that is not JSON blocks registration and says why', async () => {
    await declare();
    fireEvent.click(await screen.findByRole('button', { name: /pump-vibration/ }));
    fireEvent.change(await screen.findByLabelText('Feature sample'), {
      target: { files: [new File(['not json'], 'x.json')] },
    });
    expect(await screen.findByText(/This file is not JSON/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Upload & register/ })).toBeDisabled();
  });
});
