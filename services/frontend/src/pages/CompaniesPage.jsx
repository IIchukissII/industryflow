// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useState, useEffect, useCallback } from 'react';
import api from '../services/api';
import { companiesReading } from '../utils/reading-admin';
import { errorText } from '../utils/errors';

const detailOf = errorText;

function CompaniesPage({ nav }) {
  const [companies, setCompanies] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);
  const [showModal, setShowModal] = useState(false);
  const [editingCompany, setEditingCompany] = useState(null);
  const [formError, setFormError] = useState(null);
  const [formData, setFormData] = useState({
    company_name: '',
    is_active: true
  });

  const fetchCompanies = useCallback(async () => {
    try {
      const response = await api.get('/api/companies');
      setCompanies(response.data);
      setLoadError(null);
    } catch (error) {
      setLoadError(detailOf(error));
    } finally {
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
    setFormError(null);
    setShowModal(true);
  };

  const handleEdit = (company) => {
    setEditingCompany(company);
    setFormData({
      company_name: company.company_name,
      is_active: company.is_active
    });
    setFormError(null);
    setShowModal(true);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    try {
      if (editingCompany) {
        await api.put(`/api/companies/${editingCompany.company_id}`, formData);
      } else {
        await api.post('/api/companies', formData);
      }
      setShowModal(false);
      fetchCompanies();
    } catch (error) {
      setFormError(`The company was not saved: ${detailOf(error)}`);
    }
  };

  const handleDelete = async (company) => {
    if (!window.confirm(`Deactivate ${company.company_name}? It and all of its users become inactive and are signed out. Its data is kept, and a superuser can reactivate it.`)) {
      return;
    }
    try {
      await api.delete(`/api/companies/${company.company_id}`);
      fetchCompanies();
    } catch (error) {
      alert(`${company.company_name} was not deactivated: ${detailOf(error)}`);
    }
  };

  if (loading) {
    return <div className="adm-state"><span className="sdot pending" /> Loading companies</div>;
  }

  return (
    <div className="adm-page">
      <div className="adm-head">
        <p className="reading">
          {loadError ? `The company list could not load: ${loadError}.` : companiesReading({ companies })}
        </p>
        <button type="button" className="btn btn-primary adm-primary" onClick={handleCreate}>Add company</button>
      </div>

      {nav}

      {companies.length > 0 && (
        <div className="adm-table-wrap">
          <table className="data-table adm-table">
            <thead>
              <tr>
                <th>Company</th>
                <th>Identifier</th>
                <th>Status</th>
                <th>Created</th>
                <th><span className="adm-sr">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {companies.map(company => (
                <tr key={company.company_id}>
                  <td className="adm-name">{company.company_name}</td>
                  <td className="mono adm-dim adm-id">{company.company_id}</td>
                  <td>
                    <span className="adm-status">
                      <span className={`sdot${company.is_active ? ' ok' : ''}`} />
                      {company.is_active ? 'Active' : 'Deactivated'}
                    </span>
                  </td>
                  <td className="mono adm-dim">{new Date(company.created_at).toLocaleString()}</td>
                  <td className="adm-actions">
                    <button type="button" className="btn" onClick={() => handleEdit(company)}>Edit</button>
                    {/* A deactivated company is reactivated from Edit; deactivating it again does nothing. */}
                    {company.is_active && (
                      <button type="button" className="btn btn-danger" onClick={() => handleDelete(company)}>Deactivate</button>
                    )}
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
            className="adm-dialog" role="dialog" aria-modal="true" aria-labelledby="adm-company-title"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="adm-dialog-head">
              <h2 id="adm-company-title">{editingCompany ? 'Edit company' : 'Add a company'}</h2>
              <button type="button" className="adm-close" aria-label="Close" onClick={() => setShowModal(false)}>×</button>
            </div>
            <form onSubmit={handleSubmit}>
              <div className="adm-field">
                <label htmlFor="adm-company-name">Company name</label>
                <input
                  id="adm-company-name"
                  type="text"
                  value={formData.company_name}
                  onChange={(e) => setFormData({ ...formData, company_name: e.target.value })}
                  required
                  autoFocus
                />
              </div>

              <label className="adm-check">
                <input
                  type="checkbox"
                  checked={formData.is_active}
                  onChange={(e) => setFormData({ ...formData, is_active: e.target.checked })}
                />
                Active
              </label>

              {formError && <p className="adm-error" role="alert">{formError}</p>}

              <div className="adm-dialog-actions">
                <button type="button" className="btn" onClick={() => setShowModal(false)}>Cancel</button>
                <button type="submit" className="btn btn-primary">{editingCompany ? 'Save changes' : 'Add company'}</button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

export default CompaniesPage;
