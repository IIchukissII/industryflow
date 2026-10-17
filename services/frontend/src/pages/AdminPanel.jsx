// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { Routes, Route, Navigate, useLocation, useNavigate } from 'react-router-dom';
import CompaniesPage from './CompaniesPage';
import UsersPage from './UsersPage';
import './AdminPanel.css';

const SECTIONS = [
  { path: '/admin/companies', label: 'Companies' },
  { path: '/admin/users', label: 'Users' },
];

// Companies or users: one choice between two, so a segmented control. The pressed one follows
// the URL, so a direct link to /admin/users shows Users as chosen.
function AdminSections() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  return (
    <div className="seg adm-sections" role="group" aria-label="Administration section">
      {SECTIONS.map((s) => (
        <button
          key={s.path}
          type="button"
          aria-pressed={pathname.startsWith(s.path)}
          onClick={() => navigate(s.path)}
        >
          {s.label}
        </button>
      ))}
    </div>
  );
}

function AdminPanel() {
  const nav = <AdminSections />;
  return (
    <div className="adm">
      <Routes>
        <Route path="/" element={<Navigate to="/admin/companies" replace />} />
        <Route path="/companies" element={<CompaniesPage nav={nav} />} />
        <Route path="/users" element={<UsersPage nav={nav} />} />
      </Routes>
    </div>
  );
}

export default AdminPanel;
