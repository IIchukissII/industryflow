// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import Experiments from './Experiments';

// The page reads only the session-scoped browse path and the registry, and shows what they return:
// the tenant's experiments newest first, one experiment's runs with the metrics most runs carry, the
// model a run became, and each failure as itself.

vi.mock('../components/AppShell', () => ({
  default: ({ title, children }) => <main><h1>{title}</h1>{children}</main>,
}));

const NOW = Date.now();
const H = 3600_000;
let routes;
let calls;

function respond(body, status = 200) {
  return Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });
}

vi.mock('../services/http', () => {
  const authFetch = (path) => {
    calls.push(path);
    const hit = routes[path];
    if (!hit) return respond({}, 404);
    return respond(hit.body, hit.status || 200);
  };
  return { default: authFetch, authFetch };
});

const run = (id, name, minutesAgo, metrics, extra = {}) => ({
  run_id: id, run_name: name, status: 'FINISHED',
  start_time: NOW - minutesAgo * 60_000, end_time: NOW - minutesAgo * 60_000 + 90_000,
  metrics, params: { n_estimators: '200', contamination: '0.01' }, ...extra,
});

beforeEach(() => {
  calls = [];
  routes = {
    '/api/experiments': { body: { total: 2, experiments: [
      { name: 'older', experiment_id: '1', last_update_time: NOW - 30 * H },
      { name: 'pump anomaly', experiment_id: '2', last_update_time: NOW - 2 * H },
    ] } },
    '/api/registered-models': { body: { models: [{ name: 'pump-detector', run_id: 'r2' }] } },
    '/api/experiments/pump%20anomaly/runs': { body: { runs: [
      run('r1', 'baseline', 120, { auc: 0.71, f1: 0.5 }),
      run('r2', 'tuned', 30, { auc: 0.93, f1: 0.8 }),
      run('r3', 'broken', 10, {}, { status: 'FAILED' }),
    ] } },
    '/api/experiments/older/runs': { body: { runs: [run('o1', 'first', 3000, { loss: 0.2 })] } },
  };
});

function renderAt(path = '/experiments') {
  return render(<MemoryRouter initialEntries={[path]}><Experiments user={{}} /></MemoryRouter>);
}

describe('Experiments', () => {
  test('opens the most recently changed experiment and shows its runs', async () => {
    renderAt();
    expect(await screen.findByText('Two experiments are tracked. pump anomaly changed most recently, 2 hours ago. '
      + 'One registered model came from a tracked run.')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'pump anomaly' })).toBeInTheDocument();
    const list = screen.getByRole('navigation', { name: 'Experiments' });
    expect(within(list).getAllByRole('button').map((b) => b.textContent)).toEqual(['pump anomaly2 hours ago', 'older1 day ago']);
    expect(within(list).getByRole('button', { name: /pump anomaly/ })).toHaveAttribute('aria-current', 'true');
    const table = await screen.findByRole('table');
    // The name is a path segment, encoded. Asserted once the runs table is shown: the fetch runs in an
    // effect, after the heading has rendered.
    expect(calls).toContain('/api/experiments/pump%20anomaly/runs');
    expect(within(table).getAllByRole('columnheader').map((h) => h.textContent))
      .toEqual(['Run', 'Status', 'Started', 'Duration', 'auc', 'f1']);
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows.map((r) => within(r).getAllByRole('button')[0].textContent)).toEqual(['broken', 'tuned', 'baseline']);
    expect(within(rows[0]).getByText('Failed')).toBeInTheDocument();
    expect(within(rows[1]).getByRole('link', { name: 'Registered as pump-detector' })).toHaveAttribute('href', '/ml-models');
    expect(within(rows[0]).queryByRole('link')).toBeNull();
    expect(within(rows[1]).getByText('1 min 30 s')).toBeInTheDocument();
    expect(screen.getByText('3 runs, the latest started 10 minutes ago')).toBeInTheDocument();
  });

  test('sorts by a metric, best first, and keeps runs without it last', async () => {
    renderAt();
    const table = await screen.findByRole('table');
    fireEvent.click(within(table).getByRole('button', { name: 'auc' }));
    const names = () => within(table).getAllByRole('row').slice(1).map((r) => within(r).getAllByRole('button')[0].textContent);
    expect(names()).toEqual(['tuned', 'baseline', 'broken']);
    fireEvent.click(within(table).getByRole('button', { name: 'auc' }));
    expect(names()).toEqual(['baseline', 'tuned', 'broken']);
    expect(within(table).getByRole('columnheader', { name: 'auc' })).toHaveAttribute('aria-sort', 'ascending');
  });

  test('opens a run to show every parameter and metric', async () => {
    renderAt();
    const button = await screen.findByRole('button', { name: 'tuned' });
    fireEvent.click(button);
    expect(button).toHaveAttribute('aria-expanded', 'true');
    const metrics = screen.getByRole('heading', { name: 'Metrics' }).parentElement;
    const params = screen.getByRole('heading', { name: 'Parameters' }).parentElement;
    expect(within(params).getByText('n_estimators')).toBeInTheDocument();
    expect(within(params).getByText('200')).toBeInTheDocument();
    expect(within(metrics).getByText('auc')).toBeInTheDocument();
    expect(within(metrics).getByText('0.93')).toBeInTheDocument();
    expect(screen.getByText('Run r2')).toBeInTheDocument();
  });

  test('selects the experiment named in the address and switches on click', async () => {
    renderAt('/experiments?experiment=older');
    expect(await screen.findByRole('heading', { name: 'older' })).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: 'first' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /pump anomaly/ }));
    expect(await screen.findByRole('heading', { name: 'pump anomaly' })).toBeInTheDocument();
  });

  test('with no experiments, says where they come from and links to notebooks', async () => {
    routes['/api/experiments'] = { body: { total: 0, experiments: [] } };
    renderAt();
    expect(await screen.findByText('No experiments are tracked yet. Runs logged from a notebook appear here.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open notebooks' })).toHaveAttribute('href', '/notebooks');
    expect(screen.queryByRole('table')).toBeNull();
  });

  test('says when tracking is unavailable, and reads without the registry', async () => {
    routes['/api/experiments'] = { body: { detail: 'MLflow tracking unavailable' }, status: 502 };
    renderAt();
    expect(await screen.findByText('Experiment tracking is unavailable.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });

  test('still lists runs when the registry cannot be read', async () => {
    routes['/api/registered-models'] = { body: {}, status: 502 };
    renderAt();
    const table = await screen.findByRole('table');
    expect(within(table).queryByRole('link')).toBeNull();
    expect(screen.getByText('Two experiments are tracked. pump anomaly changed most recently, 2 hours ago.')).toBeInTheDocument();
  });
});
