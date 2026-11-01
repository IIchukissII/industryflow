// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const patch = vi.fn();
const post = vi.fn();
const get = vi.fn();
vi.mock('../services/api', () => ({ default: { patch: (...a) => patch(...a), post: (...a) => post(...a), get: (...a) => get(...a) } }));
// The canvas starfield needs a real browser (matchMedia, 2D context); jsdom has neither.
vi.mock('../components/Starfield', () => ({ default: () => null }));

import Settings from './Settings';
import Login from './Login';

beforeEach(() => {
  localStorage.setItem('user', JSON.stringify({
    email: 'ana@plant.io', role: 'engineer', company_id: 'c1', is_active: true,
  }));
});
afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.clearAllMocks();
});

function fill(label, value) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

describe('Settings password change', () => {
  test('two different passwords are refused without a request', () => {
    render(<Settings />);
    fill('New password', 'one-secret');
    fill('Repeat the new password', 'other-secret');
    fireEvent.click(screen.getByRole('button', { name: 'Change password' }));
    expect(screen.getByRole('alert').textContent).toMatch(/passwords differ/);
    expect(patch).not.toHaveBeenCalled();
  });

  test('a matching pair changes only the password of the signed-in user', async () => {
    patch.mockResolvedValue({ data: {} });
    render(<Settings />);
    fill('New password', 'new-secret');
    fill('Repeat the new password', 'new-secret');
    fireEvent.click(screen.getByRole('button', { name: 'Change password' }));
    await waitFor(() => expect(screen.getByRole('status').textContent).toMatch(/password is changed/));
    expect(patch).toHaveBeenCalledWith('/users/me', { password: 'new-secret' });
  });

  test('the server reason is shown when it refuses', async () => {
    patch.mockRejectedValue({ response: { status: 400, data: { detail: { code: 'X', reason: 'too short' } } } });
    render(<Settings />);
    fill('New password', 'x');
    fill('Repeat the new password', 'x');
    fireEvent.click(screen.getByRole('button', { name: 'Change password' }));
    await waitFor(() => expect(screen.getByRole('alert').textContent).toBe('The password was not changed: too short'));
  });
});

describe('Login', () => {
  test('renders signed out without calling the API', () => {
    localStorage.clear();
    render(<MemoryRouter><Login onLogin={() => {}} /></MemoryRouter>);
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeTruthy();
    expect(screen.getByText('No account yet? Your administrator adds you.')).toBeTruthy();
    expect(get).not.toHaveBeenCalled();
    expect(post).not.toHaveBeenCalled();
  });

  test('an account held to a password change is not sent on to the dashboards (ADR-0004 dec 11)', async () => {
    const real = window.location;
    const assign = vi.fn();
    Object.defineProperty(window, 'location', { configurable: true, value: { ...real, search: '?next=/grafana/d/x', assign } });
    try {
      post.mockResolvedValue({ data: {} });
      get.mockResolvedValue({ data: { email: 'a@b.io', password_change_required: true } });
      const onLogin = vi.fn();
      render(<MemoryRouter><Login onLogin={onLogin} /></MemoryRouter>);
      fill('Email', 'a@b.io');
      fill('Password', 'industryflow');
      fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
      await waitFor(() => expect(onLogin).toHaveBeenCalled());
      expect(assign).not.toHaveBeenCalled();
    } finally {
      Object.defineProperty(window, 'location', { configurable: true, value: real });
    }
  });
});

describe('Settings e-mail change', () => {
  test('the new address is saved and remembered', async () => {
    patch.mockResolvedValue({ data: { email: 'ana@new.io' } });
    render(<Settings />);
    fill('E-mail', 'ana@new.io');
    fireEvent.click(screen.getByRole('button', { name: 'Change e-mail' }));
    await waitFor(() => expect(screen.getByRole('status').textContent).toMatch(/e-mail address is changed/));
    expect(patch).toHaveBeenCalledWith('/users/me', { email: 'ana@new.io' });
    expect(JSON.parse(localStorage.getItem('user')).email).toBe('ana@new.io');
  });

  test('the current address cannot be submitted as a change', () => {
    render(<Settings />);
    expect(screen.getByRole('button', { name: 'Change e-mail' }).disabled).toBe(true);
  });
});
