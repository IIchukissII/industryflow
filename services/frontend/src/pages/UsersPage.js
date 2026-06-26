// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import React, { useState, useEffect } from 'react';
import axios from 'axios';

function UsersPage() {
  const [users, setUsers] = useState([]);
  const [companies, setCompanies] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [editingUser, setEditingUser] = useState(null);
  const [formData, setFormData] = useState({
    email: '',
    password: '',
    company_id: '',
    role: 'observer',
    is_active: true,
    is_superuser: false
  });

  // Fetch users and companies
  useEffect(() => {
    fetchUsers();
    fetchCompanies();
  }, []);

  const fetchUsers = async () => {
    try {
      const response = await axios.get('http://localhost:8000/api/users');
      setUsers(response.data);
      setLoading(false);
    } catch (error) {
      console.error('Error fetching users:', error);
      setLoading(false);
    }
  };

  const fetchCompanies = async () => {
    try {
      const response = await axios.get('http://localhost:8000/api/companies');
      setCompanies(response.data);
    } catch (error) {
      console.error('Error fetching companies:', error);
    }
  };

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
        // Update existing user
        await axios.patch(
          `http://localhost:8000/users/${editingUser.id}`,
          submitData
        );
      } else {
        // Create new user
        await axios.post('http://localhost:8000/auth/register', submitData);
      }
      setShowModal(false);
      fetchUsers();
    } catch (error) {
      console.error('Error saving user:', error);
      alert('Error saving user: ' + (error.response?.data?.detail || error.message));
    }
  };

  const handleDelete = async (userId) => {
    if (!window.confirm('Are you sure you want to delete this user? This cannot be undone.')) {
      return;
    }
    try {
      await axios.delete(`http://localhost:8000/users/${userId}`);
      fetchUsers();
    } catch (error) {
      console.error('Error deleting user:', error);
      alert('Error deleting user: ' + (error.response?.data?.detail || error.message));
    }
  };

  const getCompanyName = (companyId) => {
    const company = companies.find(c => c.company_id === companyId);
    return company ? company.company_name : companyId;
  };

  if (loading) {
    return <div className="loading">Loading users...</div>;
  }

  return (
    <div className="admin-page">
      <div className="page-header">
        <h1>Users Management</h1>
        <button className="btn-primary" onClick={handleCreate}>
          + Add User
        </button>
      </div>

      <div className="table-container">
        <table className="admin-table">
          <thead>
            <tr>
              <th>Email</th>
              <th>Company</th>
              <th>Role</th>
              <th>Status</th>
              <th>Superuser</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {users.map(user => (
              <tr key={user.id}>
                <td><strong>{user.email}</strong></td>
                <td>{getCompanyName(user.company_id)}</td>
                <td>
                  <span className={`role-badge role-${user.role}`}>
                    {user.role}
                  </span>
                </td>
                <td>
                  <span className={`status-badge ${user.is_active ? 'active' : 'inactive'}`}>
                    {user.is_active ? 'Active' : 'Inactive'}
                  </span>
                </td>
                <td>
                  {user.is_superuser ? '⭐ Yes' : 'No'}
                </td>
                <td>
                  <button className="btn-edit" onClick={() => handleEdit(user)}>
                    Edit
                  </button>
                  <button className="btn-delete" onClick={() => handleDelete(user.id)}>
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Modal for Create/Edit */}
      {showModal && (
        <div className="modal-overlay" onClick={() => setShowModal(false)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <h2>{editingUser ? 'Edit User' : 'Create New User'}</h2>
            <form onSubmit={handleSubmit}>
              <div className="form-group">
                <label>Email *</label>
                <input
                  type="email"
                  value={formData.email}
                  onChange={(e) => setFormData({...formData, email: e.target.value})}
                  required
                  placeholder="user@example.com"
                />
              </div>

              <div className="form-group">
                <label>Password {editingUser ? '(leave blank to keep current)' : '*'}</label>
                <input
                  type="password"
                  value={formData.password}
                  onChange={(e) => setFormData({...formData, password: e.target.value})}
                  required={!editingUser}
                  placeholder="Enter password"
                />
              </div>

              <div className="form-group">
                <label>Company *</label>
                <select
                  value={formData.company_id}
                  onChange={(e) => setFormData({...formData, company_id: e.target.value})}
                  required
                >
                  <option value="">Select Company</option>
                  {companies.map(company => (
                    <option key={company.company_id} value={company.company_id}>
                      {company.company_name}
                    </option>
                  ))}
                </select>
              </div>

              <div className="form-group">
                <label>Role *</label>
                <select
                  value={formData.role}
                  onChange={(e) => setFormData({...formData, role: e.target.value})}
                  required
                >
                  <option value="observer">Observer</option>
                  <option value="engineer">Engineer</option>
                  <option value="admin">Admin</option>
                </select>
              </div>

              <div className="form-group">
                <label>
                  <input
                    type="checkbox"
                    checked={formData.is_active}
                    onChange={(e) => setFormData({...formData, is_active: e.target.checked})}
                  />
                  {' '}Active
                </label>
              </div>

              <div className="form-group">
                <label>
                  <input
                    type="checkbox"
                    checked={formData.is_superuser}
                    onChange={(e) => setFormData({...formData, is_superuser: e.target.checked})}
                  />
                  {' '}Superuser
                </label>
              </div>

              <div className="modal-actions">
                <button type="button" className="btn-secondary" onClick={() => setShowModal(false)}>
                  Cancel
                </button>
                <button type="submit" className="btn-primary">
                  {editingUser ? 'Update' : 'Create'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

export default UsersPage;
