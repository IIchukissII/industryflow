// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useState, useEffect, useCallback } from 'react';
import api from '../services/api';

function CompaniesPage() {
  const [companies, setCompanies] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [editingCompany, setEditingCompany] = useState(null);
  const [formData, setFormData] = useState({
    company_name: '',
    is_active: true
  });

  const fetchCompanies = useCallback(async () => {
    try {
      const response = await api.get('/api/companies');
      setCompanies(response.data);
      setLoading(false);
    } catch (error) {
      console.error('Error fetching companies:', error);
      setLoading(false);
    }
  }, []);

  // Fetch companies on mount. Invoked from an inner async function so the effect body itself runs
  // no synchronous setState — the state updates land off the sync path when the request resolves.
  useEffect(() => {
    (async () => { await fetchCompanies(); })();
  }, [fetchCompanies]);

  const handleCreate = () => {
    setEditingCompany(null);
    setFormData({ company_name: '', is_active: true });
    setShowModal(true);
  };

  const handleEdit = (company) => {
    setEditingCompany(company);
    setFormData({
      company_name: company.company_name,
      is_active: company.is_active
    });
    setShowModal(true);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    try {
      if (editingCompany) {
        // Update existing company
        await api.put(
          `/api/companies/${editingCompany.company_id}`,
          formData
        );
      } else {
        // Create new company
        await api.post('/api/companies', formData);
      }
      setShowModal(false);
      fetchCompanies();
    } catch (error) {
      console.error('Error saving company:', error);
      alert('Error saving company: ' + (error.response?.data?.detail || error.message));
    }
  };

  const handleDelete = async (companyId) => {
    if (!window.confirm('Are you sure you want to delete this company? This cannot be undone.')) {
      return;
    }
    try {
      await api.delete(`/api/companies/${companyId}`);
      fetchCompanies();
    } catch (error) {
      console.error('Error deleting company:', error);
      alert('Error deleting company: ' + (error.response?.data?.detail || error.message));
    }
  };

  if (loading) {
    return <div className="loading">Loading companies...</div>;
  }

  return (
    <div className="admin-page">
      <div className="page-header">
        <h1>Companies Management</h1>
        <button className="btn-primary" onClick={handleCreate}>
          + Add Company
        </button>
      </div>

      <div className="table-container">
        <table className="admin-table">
          <thead>
            <tr>
              <th>Company Name</th>
              <th>Company ID</th>
              <th>Status</th>
              <th>Created At</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {companies.map(company => (
              <tr key={company.company_id}>
                <td><strong>{company.company_name}</strong></td>
                <td><code>{company.company_id}</code></td>
                <td>
                  <span className={`status-badge ${company.is_active ? 'active' : 'inactive'}`}>
                    {company.is_active ? 'Active' : 'Inactive'}
                  </span>
                </td>
                <td>{new Date(company.created_at).toLocaleString()}</td>
                <td>
                  <button className="btn-edit" onClick={() => handleEdit(company)}>
                    Edit
                  </button>
                  <button className="btn-delete" onClick={() => handleDelete(company.company_id)}>
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
            <h2>{editingCompany ? 'Edit Company' : 'Create New Company'}</h2>
            <form onSubmit={handleSubmit}>
              <div className="form-group">
                <label>Company Name *</label>
                <input
                  type="text"
                  value={formData.company_name}
                  onChange={(e) => setFormData({...formData, company_name: e.target.value})}
                  required
                  placeholder="Enter company name"
                />
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

              <div className="modal-actions">
                <button type="button" className="btn-secondary" onClick={() => setShowModal(false)}>
                  Cancel
                </button>
                <button type="submit" className="btn-primary">
                  {editingCompany ? 'Update' : 'Create'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

export default CompaniesPage;
