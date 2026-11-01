// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { describe, test, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';

const patch = vi.fn();
const post = vi.fn();
const get = vi.fn();
vi.mock('../services/api', () => ({ default: { patch: (...a) => patch(...a), post: (...a) => post(...a), get: (...a) => get(...a) } }));
vi.mock('../components/Starfield', () => ({ default: () => null }));

import FirstPasswordChange from './FirstPasswordChange';

const HELD = { email: 'admin@example.com', is_superuser: true, password_change_required: true };

afterEach(() => {
  cleanup();
  localStorage.clear();
  vi.clearAllMocks();
});

function fill(label, value) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

describe('First password change (ADR-0004 dec 11)', () => {
  test('a new password and e-mail are saved, then the new credentials sign in and the app continues', async () => {
    patch.mockResolvedValue({ data: {} });
    post.mockResolvedValue({ data: {} });
    get.mockResolvedValue({ data: { email: 'ops@plant.io', password_change_required: false } });
    const done = vi.fn();
    render(<FirstPasswordChange user={HELD} onDone={done} />);
    fill('E-mail', 'ops@plant.io');
    fill('New password', 'My-own-password');
    fill('Repeat the new password', 'My-own-password');
    fireEvent.click(screen.getByRole('button', { name: 'Save and continue' }));
    await waitFor(() => expect(done).toHaveBeenCalledWith({ email: 'ops@plant.io', password_change_required: false }));
    expect(patch).toHaveBeenCalledWith('/users/me', { password: 'My-own-password', email: 'ops@plant.io' });
    const form = post.mock.calls[0][1];
    expect(post.mock.calls[0][0]).toBe('/auth/login');
    expect([form.get('username'), form.get('password')]).toEqual(['ops@plant.io', 'My-own-password']);
    expect(JSON.parse(localStorage.getItem('user')).password_change_required).toBe(false);
  });

  test('an unchanged e-mail is not sent', async () => {
    patch.mockResolvedValue({ data: {} });
    post.mockResolvedValue({ data: {} });
    get.mockResolvedValue({ data: { email: HELD.email } });
    render(<FirstPasswordChange user={HELD} onDone={vi.fn()} />);
    fill('New password', 'My-own-password');
    fill('Repeat the new password', 'My-own-password');
    fireEvent.click(screen.getByRole('button', { name: 'Save and continue' }));
    await waitFor(() => expect(patch).toHaveBeenCalledWith('/users/me', { password: 'My-own-password' }));
  });

  test('two different passwords are refused without a request', () => {
    render(<FirstPasswordChange user={HELD} onDone={vi.fn()} />);
    fill('New password', 'one');
    fill('Repeat the new password', 'two');
    fireEvent.click(screen.getByRole('button', { name: 'Save and continue' }));
    expect(screen.getByRole('alert').textContent).toMatch(/passwords differ/);
    expect(patch).not.toHaveBeenCalled();
  });

  test('the server reason is shown, keeping the default is refused', async () => {
    patch.mockRejectedValue({ response: { status: 400, data: { detail: { code: 'UPDATE_USER_INVALID_PASSWORD', reason: 'Choose a password other than the current one' } } } });
    const done = vi.fn();
    render(<FirstPasswordChange user={HELD} onDone={done} />);
    fill('New password', 'industryflow');
    fill('Repeat the new password', 'industryflow');
    fireEvent.click(screen.getByRole('button', { name: 'Save and continue' }));
    await waitFor(() => expect(screen.getByRole('alert').textContent).toBe('Not changed: Choose a password other than the current one'));
    expect(done).not.toHaveBeenCalled();
  });
});

describe('After the change, a failed sign-in', () => {
  test('says the password is changed and offers sign-in, never "not changed"', async () => {
    localStorage.setItem('user', JSON.stringify(HELD));
    patch.mockResolvedValue({ data: {} });
    post.mockRejectedValue({ response: { status: 429 } });
    const done = vi.fn();
    render(<FirstPasswordChange user={HELD} onDone={done} />);
    fill('New password', 'My-own-password');
    fill('Repeat the new password', 'My-own-password');
    fireEvent.click(screen.getByRole('button', { name: 'Save and continue' }));
    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/password is changed/));
    expect(screen.getByRole('link', { name: 'Sign in' }).getAttribute('href')).toBe('/login');
    expect(localStorage.getItem('user')).toBeNull();
    expect(done).not.toHaveBeenCalled();
  });
});
