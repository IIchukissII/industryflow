// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useState } from 'react';
import './Settings.css';
import api from '../services/api';
import { API_URL, WS_URL } from '../config';
import { settingsReading } from '../utils/reading-settings';
import { detailText } from '../utils/errors';

// The gateway's 400 detail is a string, or fastapi-users' { code, reason } object.
function Fact({ label, children, mono }) {
  return (
    <div className="set-fact">
      <dt>{label}</dt>
      <dd className={mono ? 'mono' : undefined}>{children}</dd>
    </div>
  );
}

// A user may change their own password (PATCH /users/me); never their tenant or role.
function PasswordForm() {
  const [password, setPassword] = useState('');
  const [repeat, setRepeat] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null); // { ok, text }

  const submit = async (e) => {
    e.preventDefault();
    if (password !== repeat) {
      setResult({ ok: false, text: 'The two passwords differ. Type the same password in both fields.' });
      return;
    }
    setBusy(true);
    setResult(null);
    try {
      await api.patch('/users/me', { password });
      setPassword('');
      setRepeat('');
      setResult({ ok: true, text: 'Your password is changed. Use the new one next time you sign in.' });
    } catch (err) {
      const reason = err.response?.status === 400 ? detailText(err.response.data?.detail) : null;
      setResult({
        ok: false,
        text: reason
          ? `The password was not changed: ${reason}`
          : 'The password was not changed: the server did not answer. Try again in a moment.',
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="set-form" onSubmit={submit}>
      <div className="set-field">
        <label htmlFor="set-password">New password</label>
        <input
          id="set-password" type="password" autoComplete="new-password" required
          value={password} onChange={(e) => setPassword(e.target.value)}
        />
      </div>
      <div className="set-field">
        <label htmlFor="set-repeat">Repeat the new password</label>
        <input
          id="set-repeat" type="password" autoComplete="new-password" required
          value={repeat} onChange={(e) => setRepeat(e.target.value)}
        />
      </div>
      {result && (
        <p className={`set-result${result.ok ? '' : ' set-result-bad'}`} role={result.ok ? 'status' : 'alert'}>
          {result.text}
        </p>
      )}
      <div>
        <button type="submit" className="btn btn-primary set-submit" disabled={busy}>
          {busy ? 'Changing password' : 'Change password'}
        </button>
      </div>
    </form>
  );
}

function Settings() {
  // Restore the (non-sensitive) user profile synchronously at first render — nothing to await.
  const [user] = useState(() => {
    try {
      const userData = localStorage.getItem('user');
      return userData ? JSON.parse(userData) : null;
    } catch {
      return null;
    }
  });

  const apiEndpoint = API_URL || (typeof window !== 'undefined' ? window.location.origin : 'same origin');
  const role = user?.is_superuser ? 'Superuser' : user?.role;

  return (
    <div className="set">
      <p className="reading">{settingsReading({ user })}</p>

      {user && (
        <section className="set-section" aria-labelledby="set-account-h">
          <h2 id="set-account-h">Account</h2>
          <dl className="set-facts">
            <Fact label="Email">{user.email}</Fact>
            <Fact label="Role"><span className="set-cap">{role || 'Not set'}</span></Fact>
            <Fact label="Organization id" mono>{user.company_id || 'Not set'}</Fact>
            <Fact label="Status">{user.is_active ? 'Active' : 'Inactive'}</Fact>
          </dl>
          <p className="set-note">Your administrator sets your role and organization.</p>
        </section>
      )}

      {user && (
        <section className="set-section" aria-labelledby="set-password-h">
          <h2 id="set-password-h">Password</h2>
          <PasswordForm />
        </section>
      )}

      <section className="set-section" aria-labelledby="set-conn-h">
        <h2 id="set-conn-h">Connection</h2>
        <dl className="set-facts">
          <Fact label="API" mono>{apiEndpoint}</Fact>
          <Fact label="Live stream" mono>{WS_URL}/ws</Fact>
        </dl>
      </section>
    </div>
  );
}

export default Settings;
