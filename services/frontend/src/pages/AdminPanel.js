// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import React, { useState } from 'react';
import { Link, Routes, Route, Navigate } from 'react-router-dom';
import CompaniesPage from './CompaniesPage';
import UsersPage from './UsersPage';
import './AdminPanel.css';

function AdminPanel() {
  const [activeTab, setActiveTab] = useState('companies');

  return (
    <div className="admin-container">
      <div className="admin-sidebar">
        <div className="admin-header">
          <h2>⚙️ Admin Panel</h2>
          <p className="admin-subtitle">System Management</p>
        </div>
        
        <nav className="admin-nav">
          <Link 
            to="/admin/companies" 
            className={activeTab === 'companies' ? 'active' : ''}
            onClick={() => setActiveTab('companies')}
          >
            🏢 Companies
          </Link>
          <Link 
            to="/admin/users"
            className={activeTab === 'users' ? 'active' : ''}
            onClick={() => setActiveTab('users')}
          >
            👥 Users
          </Link>
          <Link to="/" className="back-to-dashboard">
            ← Back to Dashboard
          </Link>
        </nav>
      </div>

      <div className="admin-content">
        <Routes>
          <Route path="/" element={<Navigate to="/admin/companies" replace />} />
          <Route path="/companies" element={<CompaniesPage />} />
          <Route path="/users" element={<UsersPage />} />
        </Routes>
      </div>
    </div>
  );
}

export default AdminPanel;
