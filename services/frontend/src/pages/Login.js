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
        ? 'Invalid email or password'
        : 'Login failed. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-container">
      <div className="login-box">
        <div className="login-header">
          <img src="/logo-mark.svg" alt="" width="64" height="64" style={{ marginBottom: '12px' }} />
          <h1>IndustryFlow</h1>
          <p>Industrial IoT Monitoring Platform</p>
        </div>

        <form onSubmit={handleSubmit} className="login-form">
          <div className="form-group">
            <label>Email</label>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="Enter your email"
              required
              autoFocus
            />
          </div>

          <div className="form-group">
            <label>Password</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Enter your password"
              required
            />
          </div>

          {error && (
            <div className="error-message">
              {error}
            </div>
          )}

          <button type="submit" className="login-button" disabled={loading}>
            {loading ? 'Logging in...' : 'Login'}
          </button>
        </form>

        <div className="login-footer">
          <p>Need an account? Contact your system administrator.</p>
        </div>
      </div>
    </div>
  );
}

export default Login;
