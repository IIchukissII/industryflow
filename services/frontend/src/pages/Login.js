// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import './Login.css';
import api from '../services/api';

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
        <div className="login-strata" aria-hidden="true" />
        <a href="/" className="login-brand">
          <img src="/logo-mark.svg" alt="" width="32" height="32" />
          <span>IndustryFlow</span>
        </a>
        <div className="login-hero-body">
          <div className="eyebrow">Industrial IoT · Control plane</div>
          <h2 className="login-tagline">Every signal from the floor, on one ground.</h2>
          <p className="login-lede">
            Stream sensor telemetry, catch anomalies, and act on alerts across every
            tenant — in real time.
          </p>
          <ul className="login-points">
            <li><span className="mono">wss://</span> live channel streaming</li>
            <li><span className="mono">schema-per-tenant</span> isolation</li>
            <li><span className="mono">ml</span> anomaly detection</li>
          </ul>
        </div>
        <div className="login-hero-foot mono">tenant-isolated · TLS edge</div>
      </aside>

      <main className="login-panel">
        <div className="login-card">
          <img className="login-mark" src="/logo-mark.svg" alt="" width="40" height="40" />
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
