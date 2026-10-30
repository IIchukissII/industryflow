// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useState, useEffect, useCallback } from 'react';
import Icon from '../components/Icon';
import api from '../services/api';
import { usersReading } from '../utils/reading-admin';
import { errorText } from '../utils/errors';

const detailOf = errorText;
const ROLE_LABEL = { observer: 'Observer', engineer: 'Engineer', admin: 'Admin' };
const ROLE_SCOPE = {
  observer: 'Reads the company\'s data and changes nothing.',
  engineer: 'Manages equipment, sensors, alert rules and models; acknowledges and labels alerts.',
  admin: 'An engineer who also administers the company.',
};

function UsersPage({ nav }) {
  const [users, setUsers] = useState([]);
  const [companies, setCompanies] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);
  const [showModal, setShowModal] = useState(false);
  const [editingUser, setEditingUser] = useState(null);
  const [formError, setFormError] = useState(null);
  const [formData, setFormData] = useState({
    email: '',
    password: '',
    company_id: '',
    role: 'observer',
    is_active: true,
    is_superuser: false
  });

  const fetchUsers = useCallback(async () => {
    try {
      const response = await api.get('/api/users');
      setUsers(response.data);
      setLoadError(null);
    } catch (error) {
      setLoadError(detailOf(error));
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchCompanies = useCallback(async () => {
    try {
      const response = await api.get('/api/companies');
      setCompanies(response.data);
    } catch {
      // Without the list, users show their company identifier instead of its name.
    }
  }, []);

  // Fetch users and companies on mount. Invoked from an inner async function so the effect body
  // starts no synchronous setState — the updates land off the sync path as the requests resolve.
  useEffect(() => {
    (async () => { await Promise.all([fetchUsers(), fetchCompanies()]); })();
  }, [fetchUsers, fetchCompanies]);

  const handleCreate = () => {
    setEditingUser(null);
    setFormData({
      email: '',
      password: '',
      company_id: companies[0]?.company_id || '',
      role: 'observer',
      is_active: true,
      is_superuser: false
    });
    setFormError(null);
    setShowModal(true);
  };

  const handleEdit = (user) => {
    setEditingUser(user);
    setFormData({
      email: user.email,
      password: '',
      company_id: user.company_id,
      role: user.role,
      is_active: user.is_active,
      is_superuser: user.is_superuser
    });
    setFormError(null);
    setShowModal(true);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    try {
      const submitData = { ...formData };

      // Don't send password if editing and password field is empty
      if (editingUser && !submitData.password) {
        delete submitData.password;
      }

      if (editingUser) {
        await api.patch(`/api/users/${editingUser.id}`, submitData);
      } else {
        await api.post('/api/users', submitData);
      }
      setShowModal(false);
      fetchUsers();
    } catch (error) {
      setFormError(`The user was not saved: ${detailOf(error)}`);
    }
  };

  const handleDelete = async (user) => {
    if (!window.confirm(`Delete ${user.email}? This cannot be undone.`)) {
      return;
    }
    try {
      await api.delete(`/api/users/${user.id}`);
      fetchUsers();
    } catch (error) {
      alert(`${user.email} was not deleted: ${detailOf(error)}`);
    }
  };

  const getCompanyName = (companyId) => {
    const company = companies.find(c => c.company_id === companyId);
    return company ? company.company_name : companyId;
  };

  if (loading) {
    return <div className="adm-state"><span className="sdot pending" /> Loading users</div>;
  }

  return (
    <div className="adm-page">
      <div className="adm-head">
        <p className="reading">
          {loadError ? `The user list could not load: ${loadError}.` : usersReading({ users, companies })}
        </p>
        <button type="button" className="btn btn-primary adm-primary" onClick={handleCreate}>Add user</button>
      </div>

      {nav}

      {users.length > 0 && (
        <div className="adm-table-wrap">
          <table className="data-table adm-table">
            <thead>
              <tr>
                <th>Email</th>
                <th>Company</th>
                <th>Role</th>
                <th>Status</th>
                <th><span className="adm-sr">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {users.map(user => (
                <tr key={user.id}>
                  <td className="adm-name">
                    {user.email}
                    {user.is_superuser && (
                      <span className="adm-super"><Icon name="star" size={12} /> Superuser</span>
                    )}
                  </td>
                  <td>{getCompanyName(user.company_id)}</td>
                  <td>{ROLE_LABEL[user.role] || user.role}</td>
                  <td>
                    <span className="adm-status">
                      <span className={`sdot${user.is_active ? ' ok' : ''}`} />
                      {user.is_active ? 'Active' : 'Inactive'}
                    </span>
                  </td>
                  <td className="adm-actions">
                    <button type="button" className="btn" onClick={() => handleEdit(user)}>Edit</button>
                    <button type="button" className="btn btn-danger" onClick={() => handleDelete(user)}>Delete user</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {showModal && (
        <div className="adm-overlay" onClick={() => setShowModal(false)}>
          <div
            className="adm-dialog" role="dialog" aria-modal="true" aria-labelledby="adm-user-title"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="adm-dialog-head">
              <h2 id="adm-user-title">{editingUser ? 'Edit user' : 'Add a user'}</h2>
              <button type="button" className="adm-close" aria-label="Close" onClick={() => setShowModal(false)}>×</button>
            </div>
            <form onSubmit={handleSubmit}>
              <div className="adm-field">
                <label htmlFor="adm-user-email">Email</label>
                <input
                  id="adm-user-email"
                  type="email"
                  value={formData.email}
                  onChange={(e) => setFormData({ ...formData, email: e.target.value })}
                  required
                  autoFocus
                  placeholder="name@example.com"
                />
              </div>

              <div className="adm-field">
                <label htmlFor="adm-user-password">
                  Password{editingUser && <span className="adm-hint"> Leave blank to keep the current one</span>}
                </label>
                <input
                  id="adm-user-password"
                  type="password"
                  value={formData.password}
                  onChange={(e) => setFormData({ ...formData, password: e.target.value })}
                  required={!editingUser}
                  autoComplete="new-password"
                />
              </div>

              <div className="adm-field">
                <label htmlFor="adm-user-company">Company</label>
                <select
                  id="adm-user-company"
                  value={formData.company_id}
                  onChange={(e) => setFormData({ ...formData, company_id: e.target.value })}
                  required
                >
                  <option value="">Choose a company</option>
                  {companies.map(company => (
                    <option key={company.company_id} value={company.company_id}>
                      {company.company_name}
                    </option>
                  ))}
                </select>
              </div>

              <div className="adm-field">
                <label htmlFor="adm-user-role">Role</label>
                <select
                  id="adm-user-role"
                  value={formData.role}
                  onChange={(e) => setFormData({ ...formData, role: e.target.value })}
                  aria-describedby="adm-user-role-scope"
                  required
                >
                  {Object.entries(ROLE_LABEL).map(([value, label]) => (
                    <option key={value} value={value}>{label}</option>
                  ))}
                </select>
                <p className="adm-scope" id="adm-user-role-scope">{ROLE_SCOPE[formData.role]}</p>
              </div>

              <label className="adm-check">
                <input
                  type="checkbox"
                  checked={formData.is_active}
                  onChange={(e) => setFormData({ ...formData, is_active: e.target.checked })}
                />
                Active
              </label>

              <label className="adm-check">
                <input
                  type="checkbox"
                  checked={formData.is_superuser}
                  onChange={(e) => setFormData({ ...formData, is_superuser: e.target.checked })}
                />
                Superuser, across every company
              </label>

              {formError && <p className="adm-error" role="alert">{formError}</p>}

              <div className="adm-dialog-actions">
                <button type="button" className="btn" onClick={() => setShowModal(false)}>Cancel</button>
                <button type="submit" className="btn btn-primary">{editingUser ? 'Save changes' : 'Add user'}</button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

export default UsersPage;
