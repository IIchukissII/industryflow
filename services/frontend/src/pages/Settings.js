// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import React, { useState, useEffect } from 'react';
import './Settings.css';
import { API_URL, WS_URL } from '../config';

function Settings() {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const userData = localStorage.getItem('user');
    if (userData) {
      setUser(JSON.parse(userData));
    }
    setLoading(false);
  }, []);

  if (loading) return <div className="settings-loading">Loading...</div>;

  return (
    <div className="settings-container">
      <div className="settings-header">
        <h1>Settings</h1>
        <p className="settings-subtitle">Manage your account and preferences</p>
      </div>

      <div className="settings-content">
        <div className="settings-section">
          <h2>Account Information</h2>
          <div className="settings-grid">
            <div className="settings-item">
              <label>Email</label>
              <div className="settings-value">{user?.email}</div>
            </div>
            <div className="settings-item">
              <label>Role</label>
              <div className="settings-value">{user?.role}</div>
            </div>
            <div className="settings-item">
              <label>Company ID</label>
              <div className="settings-value">{user?.company_id}</div>
            </div>
            <div className="settings-item">
              <label>Account Status</label>
              <div className="settings-value">
                <span className={`status-badge ${user?.is_active ? 'active' : 'inactive'}`}>
                  {user?.is_active ? 'Active' : 'Inactive'}
                </span>
              </div>
            </div>
          </div>
        </div>

        <div className="settings-section">
          <h2>Platform Configuration</h2>
          <div className="settings-grid">
            <div className="settings-item">
              <label>API Endpoint</label>
              <div className="settings-value">{API_URL}</div>
            </div>
            <div className="settings-item">
              <label>WebSocket Endpoint</label>
              <div className="settings-value">{WS_URL}/ws</div>
            </div>
          </div>
        </div>

        <div className="settings-section">
          <h2>Actions</h2>
          <div className="settings-actions">
            <button className="btn-secondary">Change Password</button>
            <button className="btn-secondary">Export Data</button>
            <button className="btn-danger">Delete Account</button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default Settings;
