// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useState, useEffect, useCallback } from 'react';
import { humanize } from '../utils/humanize';
import EquipmentConfigModal from '../components/EquipmentConfigModal';
import { getEquipment, getEquipmentById, getEquipmentSensors, deleteEquipment } from '../services/equipmentApi';
import './Equipment.css';
import { canWrite } from '../utils/roles';
import { equipmentReading } from '../utils/reading';
import { sensorLabel } from '../utils/sensors';
import { errorText } from '../utils/errors';

// "grow-cabinet" -> "Grow cabinet"

// The machine's own small mandala: one node per configured sensor around a gold core.
function SensorRing({ count }) {
  const n = Math.max(count, 0);
  const nodes = Array.from({ length: n }, (_, i) => {
    const a = ((-90 + (i * 360) / n) * Math.PI) / 180;
    return { x: 60 + 46 * Math.cos(a), y: 60 + 46 * Math.sin(a) };
  });
  return (
    <svg className="eq-ring" viewBox="0 0 120 120" role="img" aria-label={`${n} sensors`}>
      <defs>
        <radialGradient id="eqGlow" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#e6c67c" stopOpacity="0.5" />
          <stop offset="100%" stopColor="#cfa94e" stopOpacity="0" />
        </radialGradient>
      </defs>
      <circle cx="60" cy="60" r="34" fill="url(#eqGlow)" />
      <circle cx="60" cy="60" r="46" fill="none" stroke="#2a3248" strokeWidth="0.8" />
      {nodes.map((p, i) => <line key={`l${i}`} x1="60" y1="60" x2={p.x} y2={p.y} stroke="#232a3d" strokeWidth="0.6" />)}
      {nodes.map((p, i) => <circle key={`n${i}`} cx={p.x} cy={p.y} r="3" fill="#0d1222" stroke="#5c6478" strokeWidth="1.2" />)}
      <circle cx="60" cy="60" r="2.6" fill="#fff6dc" />
    </svg>
  );
}

function Fact({ label, children, mono }) {
  return (
    <div className="eq-fact">
      <dt>{label}</dt>
      <dd className={mono ? 'mono' : undefined}>{children}</dd>
    </div>
  );
}

function Equipment() {
  // Creating, editing and deleting equipment needs engineer or admin (observer is read-only).
  const [writable] = useState(() => canWrite());
  const [equipment, setEquipment] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedEquipment, setSelectedEquipment] = useState(null);
  const [showModal, setShowModal] = useState(false);
  const [showConfigModal, setShowConfigModal] = useState(false);
  const [editingEquipment, setEditingEquipment] = useState(null);

  const fetchEquipment = useCallback(async () => {
    try {
      const data = await getEquipment();
      setEquipment(data);
      setLoading(false);
    } catch (err) {
      setError(err.message);
      setLoading(false);
    }
  }, []);

  // Fetch on mount. Invoked from an inner async function so the effect body itself runs no
  // synchronous setState — the equipment list lands off the sync path when the request resolves.
  useEffect(() => {
    (async () => { await fetchEquipment(); })();
  }, [fetchEquipment]);

  const viewEquipmentDetails = async (equipmentId) => {
    try {
      const data = await getEquipmentById(equipmentId);
      // Sensor names and units for the list; the configured ids stand in if that call fails.
      let sensors = null;
      try { sensors = await getEquipmentSensors(equipmentId); } catch { sensors = null; }
      setSelectedEquipment({ ...data, sensorRows: Array.isArray(sensors) ? sensors : null });
      setShowModal(true);
    } catch (err) {
      alert('The machine details could not load: ' + err.message);
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

  const handleDelete = async (equip) => {
    if (!window.confirm(`Delete ${equip.name}? Its configuration is removed; stored readings stay.`)) {
      return;
    }
    try {
      await deleteEquipment(equip.equipment_id);
      await fetchEquipment();
    } catch (err) {
      alert('The machine could not be deleted: ' + errorText(err));
    }
  };

  if (loading) return <div className="eq-state"><span className="sdot pending" /> Loading equipment</div>;
  if (error) return <div className="eq-state eq-state-bad">The equipment list could not load: {error}</div>;

  return (
    <div className="eq">
      <div className="eq-head">
        <p className="reading">{equipmentReading({ equipment })}</p>
        {writable && (
          <button type="button" className="btn btn-primary eq-add" onClick={handleCreateNew}>Add equipment</button>
        )}
      </div>

      <div className="eq-list">
        {equipment.map((equip) => (
          <article key={equip.equipment_id} className="eq-plate" aria-labelledby={`eq-${equip.equipment_id}`}>
            <SensorRing count={Number(equip.sensor_count) || 0} />
            <div className="eq-body">
              <div>
                <h2 id={`eq-${equip.equipment_id}`}>{equip.name}</h2>
                <div className="eq-type">{humanize(equip.equipment_type)}</div>
                {equip.description && <p className="eq-desc">{equip.description}</p>}
              </div>
              <dl className="eq-facts">
                <Fact label="Sensors" mono>{equip.sensor_count}</Fact>
                <Fact label="Site">{equip.site_id || 'Not set'}</Fact>
                <Fact label="Location">{equip.location || 'Not set'}</Fact>
              </dl>
              <div className="eq-actions">
                <a className="btn btn-primary" href="/">Show on Core</a>
                <button type="button" className="btn" onClick={() => viewEquipmentDetails(equip.equipment_id)}>
                  Details
                </button>
                {writable && (
                  <>
                    <button type="button" className="btn" onClick={() => handleEdit(equip)}>Edit</button>
                    <button type="button" className="btn btn-danger eq-delete" onClick={() => handleDelete(equip)}>
                      Delete machine
                    </button>
                  </>
                )}
              </div>
            </div>
          </article>
        ))}
      </div>

      {showModal && selectedEquipment && (
        <div className="modal-overlay" onClick={() => setShowModal(false)}>
          <div
            className="modal-content" role="dialog" aria-modal="true" aria-label={selectedEquipment.name}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="modal-header">
              <h2>{selectedEquipment.name}</h2>
              <button type="button" className="modal-close" aria-label="Close" onClick={() => setShowModal(false)}>×</button>
            </div>
            <div className="modal-body">
              <section className="detail-section">
                <h3>Machine</h3>
                <dl className="detail-grid">
                  <Fact label="Identifier" mono>{selectedEquipment.equipment_id}</Fact>
                  <Fact label="Type">{humanize(selectedEquipment.equipment_type)}</Fact>
                  <Fact label="Site">{selectedEquipment.site_id || 'Not set'}</Fact>
                  <Fact label="Location">{selectedEquipment.location || 'Not set'}</Fact>
                </dl>
              </section>
              <section className="detail-section">
                <h3>Sensors ({selectedEquipment.sensorRows?.length ?? selectedEquipment.expected_sensors?.length ?? 0})</h3>
                <div className="sensor-list">
                  {selectedEquipment.sensorRows
                    ? selectedEquipment.sensorRows.map((s) => (
                      <span key={s.sensor_id} className="sensor-chip" title={s.display_name ? s.sensor_name : undefined}>
                        {sensorLabel(s)}{s.unit ? <span className="sensor-chip-unit"> {s.unit}</span> : null}
                      </span>
                    ))
                    : selectedEquipment.expected_sensors?.map((id) => (
                      <span key={id} className="sensor-chip mono">{id}</span>
                    ))}
                </div>
              </section>
            </div>
          </div>
        </div>
      )}

      {/* Config modal, keyed by target so opening a different machine (or switching between
          create and edit) remounts it with fresh, correctly initialised form state. */}
      <EquipmentConfigModal
        key={editingEquipment?.equipment_id ?? 'new'}
        isOpen={showConfigModal}
        onClose={() => {
          setShowConfigModal(false);
          setEditingEquipment(null);
        }}
        onSuccess={fetchEquipment}
        editEquipment={editingEquipment}
      />
    </div>
  );
}

export default Equipment;
