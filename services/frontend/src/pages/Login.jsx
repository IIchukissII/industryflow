// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import './Login.css';
import api from '../services/api';
import Mandala from '../components/Mandala';
import Starfield from '../components/Starfield';

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
      navigate('/');
    } catch (err) {
      const status = err.response?.status;
      setError(status === 400 || status === 401
        ? 'Invalid email or password.'
        : 'Sign-in failed. Please try again.');
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
          <h1 className="login-wordmark">Industry<span className="wm-accent">Flow</span></h1>
          <p className="login-tagline">The still centre in the turning world.</p>
          <p className="login-lede">
            Every sensor stream converges inward — flux resolved into meaning at a
            luminous core.
          </p>
        </div>
      </aside>

      <main className="login-panel">
        <div className="login-card">
          <div className="eyebrow">Industrial IoT · Control plane</div>
          <h1>Sign in</h1>
          <p className="login-sub">Access your IndustryFlow workspace.</p>

          <form onSubmit={handleSubmit} className="login-form">
            <div className="form-group">
              <label htmlFor="email">Email</label>
              <input
                id="email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@company.com"
                required
                autoFocus
              />
            </div>

            <div className="form-group">
              <label htmlFor="password">Password</label>
              <input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                required
              />
            </div>

            {error && <div className="login-error">{error}</div>}

            <button type="submit" className="btn-primary login-submit" disabled={loading}>
              {loading ? 'Signing in…' : 'Sign in'}
            </button>
          </form>

          <div className="login-foot">Need access? Contact your administrator.</div>
        </div>
      </main>
    </div>
  );
}

export default Login;
