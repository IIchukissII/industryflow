import React, { useState, useEffect } from 'react';
import Header from '../components/Header';
import EquipmentConfigModal from '../components/EquipmentConfigModal';
import { getEquipment, getEquipmentById, deleteEquipment } from '../services/equipmentApi';
import './Equipment.css';

const API_BASE_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000';

function Equipment() {
  const [equipment, setEquipment] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedEquipment, setSelectedEquipment] = useState(null);
  const [showModal, setShowModal] = useState(false);
  const [showConfigModal, setShowConfigModal] = useState(false);
  const [editingEquipment, setEditingEquipment] = useState(null);
  const [user, setUser] = useState(null);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    // Get user from localStorage
    const userData = localStorage.getItem('user');
    if (userData) {
      setUser(JSON.parse(userData));
    }

    // Check API connection
    fetch(`${API_BASE_URL}/health`)
      .then(res => res.json())
      .then(() => setConnected(true))
      .catch(() => setConnected(false));

    fetchEquipment();
  }, []);

  const fetchEquipment = async () => {
    try {
      const data = await getEquipment();
      setEquipment(data);
      setLoading(false);
    } catch (err) {
      setError(err.message);
      setLoading(false);
    }
  };

  const viewEquipmentDetails = async (equipmentId) => {
    try {
      const data = await getEquipmentById(equipmentId);
      setSelectedEquipment(data);
      setShowModal(true);
    } catch (err) {
      alert('Error loading equipment details: ' + err.message);
    }
  };

  const handleCreateNew = () => {
    setEditingEquipment(null);
    setShowConfigModal(true);
  };

  const handleEdit = (equip) => {
    setEditingEquipment(equip);
    setShowConfigModal(true);
  };

  const handleDelete = async (equipmentId) => {
    if (!window.confirm('Are you sure you want to delete this equipment?')) {
      return;
    }

    try {
      await deleteEquipment(equipmentId);
      await fetchEquipment();
      alert('Equipment deleted successfully');
    } catch (err) {
      alert('Error deleting equipment: ' + (err.response?.data?.detail || err.message));
    }
  };

  const handleConfigSuccess = () => {
    fetchEquipment();
  };

  if (loading) return <div className="equipment-loading">Loading equipment...</div>;
  if (error) return <div className="equipment-error">Error: {error}</div>;

  return (
    <div className="App">
      <Header user={user} connected={connected} />

      <div className="equipment-container">
        <div className="equipment-header">
          <div>
            <h1>Equipment Management</h1>
            <p className="equipment-subtitle">Manage industrial equipment and sensor configurations</p>
          </div>
          <button className="btn-create" onClick={handleCreateNew}>
            + Create New Equipment
          </button>
        </div>

        <div className="equipment-grid">
          {equipment.map((equip) => (
            <div key={equip.equipment_id} className="equipment-card">
              <div className="equipment-card-header">
                <h3>{equip.name}</h3>
                <span className="equipment-badge">{equip.equipment_type}</span>
              </div>
              
              <div className="equipment-card-body">
                <div className="equipment-stat">
                  <span className="stat-label">Sensors:</span>
                  <span className="stat-value">{equip.sensor_count}</span>
                </div>
                
                <div className="equipment-stat">
                  <span className="stat-label">Location:</span>
                  <span className="stat-value">{equip.location || 'N/A'}</span>
                </div>
                
                <div className="equipment-stat">
                  <span className="stat-label">Site:</span>
                  <span className="stat-value">{equip.site_id || 'N/A'}</span>
                </div>

                <div className="equipment-stat">
                  <span className="stat-label">Batch Timeout:</span>
                  <span className="stat-value">{equip.batch_timeout_seconds}s</span>
                </div>

                {equip.description && (
                  <p className="equipment-description">{equip.description}</p>
                )}
              </div>

              <div className="equipment-card-footer">
                <button 
                  className="btn-view-details"
                  onClick={() => viewEquipmentDetails(equip.equipment_id)}
                >
                  View Details
                </button>
                <button 
                  className="btn-edit"
                  onClick={() => handleEdit(equip)}
                >
                  Edit
                </button>
                <button 
                  className="btn-delete"
                  onClick={() => handleDelete(equip.equipment_id)}
                >
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>

        {equipment.length === 0 && (
          <div className="equipment-empty">
            <p>No equipment configured</p>
            <button className="btn-primary" onClick={handleCreateNew}>
              Create Your First Equipment
            </button>
          </div>
        )}

        {/* Details Modal */}
        {showModal && selectedEquipment && (
          <div className="modal-overlay" onClick={() => setShowModal(false)}>
            <div className="modal-content" onClick={(e) => e.stopPropagation()}>
              <div className="modal-header">
                <h2>{selectedEquipment.name}</h2>
                <button className="modal-close" onClick={() => setShowModal(false)}>×</button>
              </div>
              
              <div className="modal-body">
                <div className="detail-section">
                  <h3>Equipment Information</h3>
                  <div className="detail-grid">
                    <div className="detail-item">
                      <span className="detail-label">Equipment ID:</span>
                      <span className="detail-value">{selectedEquipment.equipment_id}</span>
                    </div>
                    <div className="detail-item">
                      <span className="detail-label">Type:</span>
                      <span className="detail-value">{selectedEquipment.equipment_type}</span>
                    </div>
                    <div className="detail-item">
                      <span className="detail-label">Location:</span>
                      <span className="detail-value">{selectedEquipment.location || 'N/A'}</span>
                    </div>
                    <div className="detail-item">
                      <span className="detail-label">Site ID:</span>
                      <span className="detail-value">{selectedEquipment.site_id || 'N/A'}</span>
                    </div>
                  </div>
                </div>

                <div className="detail-section">
                  <h3>Batch Configuration</h3>
                  <div className="detail-grid">
                    <div className="detail-item">
                      <span className="detail-label">Sensor Count:</span>
                      <span className="detail-value">{selectedEquipment.sensor_count}</span>
                    </div>
                    <div className="detail-item">
                      <span className="detail-label">Batch Timeout:</span>
                      <span className="detail-value">{selectedEquipment.batch_timeout_seconds}s</span>
                    </div>
                    <div className="detail-item">
                      <span className="detail-label">Require Complete:</span>
                      <span className="detail-value">{selectedEquipment.require_complete_batch ? 'Yes' : 'No'}</span>
                    </div>
                    <div className="detail-item">
                      <span className="detail-label">Min Sensors (Partial):</span>
                      <span className="detail-value">{selectedEquipment.min_sensors_for_partial || 'N/A'}</span>
                    </div>
                  </div>
                </div>

                <div className="detail-section">
                  <h3>Expected Sensors ({selectedEquipment.expected_sensors?.length || 0})</h3>
                  <div className="sensor-list">
                    {selectedEquipment.expected_sensors?.map((sensor, idx) => (
                      <span key={idx} className="sensor-chip">{sensor}</span>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Config Modal */}
        <EquipmentConfigModal
          isOpen={showConfigModal}
          onClose={() => {
            setShowConfigModal(false);
            setEditingEquipment(null);
          }}
          onSuccess={handleConfigSuccess}
          editEquipment={editingEquipment}
        />
      </div>
    </div>
  );
}

export default Equipment;
