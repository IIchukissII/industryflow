// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import './Login.css';
import api from '../services/api';
import Mandala from '../components/Mandala';
import Starfield from '../components/Starfield';
import { returnPath } from '../services/returnPath';

function Login({ onLogin }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      // The server sets httpOnly access + refresh cookies and a JS-readable CSRF cookie;
      // no token is stored in JS (ADR-0004 dec 3).
      const form = new URLSearchParams();
      form.append('username', email);
      form.append('password', password);
      await api.post('/auth/login', form, {
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      });

      // Identify the user (authenticated via the cookie); store only non-sensitive profile.
      const me = await api.get('/users/me');
      localStorage.setItem('user', JSON.stringify(me.data));
      onLogin(me.data);
      // Back to the dashboards when the edge sent the visitor here from them (ADR-0016 dec 7).
      const next = returnPath(window.location.search);
      if (next) window.location.assign(next);
      else navigate('/');
    } catch (err) {
      const status = err.response?.status;
      setError(status === 400 || status === 401
        ? 'That email and password do not match an account. Check both and try again.'
        : 'The server did not answer. Try again in a moment.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login">
      <aside className="login-hero">
        <Starfield className="login-stars" />
        <div className="login-hero-stack">
          <Mandala size={300} className="login-hero-mandala" />
          <p className="login-wordmark">Industry<span className="wm-accent">Flow</span></p>
          <p className="login-tagline">The still centre in the turning world.</p>
          <p className="login-lede">
            Every sensor stream converges inward, flux resolved into meaning at a luminous core.
          </p>
        </div>
      </aside>

      <main className="login-panel">
        <div className="login-card">
          <h1 className="login-title">Sign in</h1>
          <p className="login-sub">Use the email and password your administrator gave you.</p>

          <form onSubmit={handleSubmit} className="login-form">
            <div className="login-field">
              <label htmlFor="email">Email</label>
              <input
                id="email"
                type="email"
                autoComplete="username"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@company.com"
                required
                autoFocus
              />
            </div>

            <div className="login-field">
              <label htmlFor="password">Password</label>
              <input
                id="password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </div>

            {error && <p className="login-error" role="alert">{error}</p>}

            <button type="submit" className="btn btn-primary login-submit" disabled={loading}>
              {loading ? 'Signing in' : 'Sign in'}
            </button>
          </form>

          <p className="login-foot">No account yet? Your administrator adds you.</p>
        </div>
      </main>
    </div>
  );
}

export default Login;
