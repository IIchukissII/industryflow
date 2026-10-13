// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { canWrite } from '../utils/roles';
import AlertHistory from './AlertHistory';
import AlertRules from './AlertRules';
import Equipment from './Equipment';

const PAGES = { AlertHistory, AlertRules, Equipment };

// An observer is read-only (the gateway answers 403 to its writes): the pages do not offer it
// controls that would only fail. engineer, admin and superusers keep them.

const ALERT = {
  alert_id: 'a1', rule_id: 'r1', rule_name: 'Hot', severity: 'high', detection_type: 'threshold',
  message: 'too hot', time: '2026-10-13T10:00:00Z', acknowledged: false, label_verdict: null,
};
const RULE = {
  rule_id: 'r1', name: 'Hot', detection_type: 'threshold', severity: 'high', enabled: true,
  condition: 'greater_than', threshold: 10,
};
const EQUIPMENT = {
  equipment_id: 'e1', name: 'Pump', equipment_type: 'pump', sensor_count: 1, batch_timeout_seconds: 5,
};

function respond(body) {
  return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
}

vi.mock('../services/http', () => ({
  default: (path) => {
    if (path.startsWith('/api/alerts/label-metrics')) return respond({ overall: {}, series: [] });
    if (path.startsWith('/api/alerts')) return respond([ALERT]);
    if (path.startsWith('/api/alert-rules')) return respond([RULE]);
    return respond([]);
  },
}));
vi.mock('../services/equipmentApi', () => ({
  getEquipment: () => Promise.resolve([EQUIPMENT]),
  getEquipmentById: () => Promise.resolve(EQUIPMENT),
  deleteEquipment: () => Promise.resolve(),
}));

function signIn(user) {
  localStorage.setItem('user', JSON.stringify(user));
}

async function renderPage(name) {
  const Page = PAGES[name];
  render(<MemoryRouter><Page /></MemoryRouter>);
}

describe('canWrite', () => {
  test('observer cannot; engineer, admin and superuser can', () => {
    expect(canWrite({ role: 'observer' })).toBe(false);
    expect(canWrite({ role: 'engineer' })).toBe(true);
    expect(canWrite({ role: 'admin' })).toBe(true);
    expect(canWrite({ role: 'observer', is_superuser: true })).toBe(true);
    expect(canWrite(null)).toBe(false);
  });
});

describe('write controls by role', () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => cleanup());

  test('observer: alerts can be read but not acknowledged or labelled', async () => {
    signIn({ role: 'observer' });
    await renderPage('AlertHistory');
    expect(await screen.findByText('too hot')).toBeInTheDocument();
    expect(screen.queryByText('Acknowledge')).toBeNull();
    for (const label of ['Real', 'False', 'Unsure']) {
      expect(screen.getByRole('button', { name: label })).toBeDisabled();
    }
  });

  test('engineer: alerts can be acknowledged and labelled', async () => {
    signIn({ role: 'engineer' });
    await renderPage('AlertHistory');
    expect(await screen.findByText('Acknowledge')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Real' })).toBeEnabled();
  });

  test('observer: no rule create/edit/delete', async () => {
    signIn({ role: 'observer' });
    await renderPage('AlertRules');
    expect(await screen.findByText('Hot')).toBeInTheDocument();
    expect(screen.queryByText('+ New Rule')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Edit' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Delete' })).toBeNull();
  });

  test('admin: rule create/edit/delete offered', async () => {
    signIn({ role: 'admin' });
    await renderPage('AlertRules');
    expect(await screen.findByText('Hot')).toBeInTheDocument();
    expect(screen.getByText('+ New Rule')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Edit' })).toBeInTheDocument();
  });

  test('observer: equipment can be viewed, not created, edited or deleted', async () => {
    signIn({ role: 'observer' });
    await renderPage('Equipment');
    expect(await screen.findByText('Pump')).toBeInTheDocument();
    expect(screen.getByText('View Details')).toBeInTheDocument();
    expect(screen.queryByText('+ Create New Equipment')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Edit' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Delete' })).toBeNull();
  });

  test('engineer: equipment create/edit/delete offered', async () => {
    signIn({ role: 'engineer' });
    await renderPage('Equipment');
    expect(await screen.findByText('Pump')).toBeInTheDocument();
    expect(screen.getByText('+ Create New Equipment')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Delete' })).toBeInTheDocument();
  });
});
