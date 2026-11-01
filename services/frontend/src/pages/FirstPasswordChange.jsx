// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useState } from 'react';
import './Login.css';
import './Settings.css';
import api from '../services/api';
import Mandala from '../components/Mandala';
import Starfield from '../components/Starfield';
import { detailText } from '../utils/errors';

// An account signed in with the published default password sets its own before anything else
// (ADR-0004 dec 11): until then the server answers nothing but this. Changing the password ends every
// session, so the new credentials sign in again here and the app continues.
export default function FirstPasswordChange({ user, onDone }) {
  const [email, setEmail] = useState(user?.email || '');
  const [password, setPassword] = useState('');
  const [repeat, setRepeat] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const submit = async (e) => {
    e.preventDefault();
    setError('');
    if (password !== repeat) {
      setError('The two passwords differ. Type the same password in both fields.');
      return;
    }
    setBusy(true);
    try {
      const change = { password };
      if (email.trim() && email.trim() !== user?.email) change.email = email.trim();
      await api.patch('/users/me', change);
      const form = new URLSearchParams();
      form.append('username', change.email || user.email);
      form.append('password', password);
      await api.post('/auth/login', form, { headers: { 'Content-Type': 'application/x-www-form-urlencoded' } });
      const me = await api.get('/users/me');
      localStorage.setItem('user', JSON.stringify(me.data));
      onDone(me.data);
    } catch (err) {
      const reason = err.response?.status === 400 ? detailText(err.response.data?.detail) : null;
      setError(reason
        ? `Not changed: ${reason}`
        : 'Not changed: the server did not answer. Try again in a moment.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login">
      <aside className="login-hero">
        <Starfield className="login-stars" />
        <div className="login-hero-stack">
          <Mandala size={300} className="login-hero-mandala" />
          <p className="login-wordmark">Industry<span className="wm-accent">Flow</span></p>
        </div>
      </aside>
      <main className="login-panel">
        <div className="login-card">
          <form className="set-form" onSubmit={submit} aria-labelledby="first-h">
            <h1 id="first-h">Set your own password</h1>
            <p className="set-note">
              This account signed in with the default password. Choose your own before anything else;
              you may also change the e-mail address it signs in with.
            </p>
            <div className="set-field">
              <label htmlFor="first-email">E-mail</label>
              <input id="first-email" type="email" autoComplete="username" required
                value={email} onChange={(e) => setEmail(e.target.value)} />
            </div>
            <div className="set-field">
              <label htmlFor="first-password">New password</label>
              <input id="first-password" type="password" autoComplete="new-password" required
                value={password} onChange={(e) => setPassword(e.target.value)} />
            </div>
            <div className="set-field">
              <label htmlFor="first-repeat">Repeat the new password</label>
              <input id="first-repeat" type="password" autoComplete="new-password" required
                value={repeat} onChange={(e) => setRepeat(e.target.value)} />
            </div>
            {error && <p className="set-result set-result-bad" role="alert">{error}</p>}
            <div>
              <button type="submit" className="btn btn-primary set-submit" disabled={busy}>
                {busy ? 'Saving' : 'Save and continue'}
              </button>
            </div>
          </form>
        </div>
      </main>
    </div>
  );
}
