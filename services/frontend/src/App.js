// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import React, { useState, useEffect } from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import './App.css';
import { getLatestSensors } from './services/api';
import { API_URL } from './config';
import websocketService from './services/websocket';
import SensorChart from './components/SensorChart';
import AppShell from './components/AppShell';
import Icon from './components/Icon';
import AlertRules from './pages/AlertRules';
import AlertHistory from './pages/AlertHistory';
import AdminPanel from './pages/AdminPanel';
import Equipment from './pages/Equipment';
import Settings from './pages/Settings';
import MLModels from './pages/MLModels';
import Login from './pages/Login';

function ProtectedRoute({ children, user }) {
  if (!user) {
    return <Navigate to="/login" replace />;
  }
  return children;
}

function Dashboard({ user }) {
  const [sensors, setSensors] = useState({});
  const [connected, setConnected] = useState(false);
  const [wsConnected, setWsConnected] = useState(false);
  const [lastUpdate, setLastUpdate] = useState(null);
  const [selectedEquipment, setSelectedEquipment] = useState(null);
  const [selectedSensor, setSelectedSensor] = useState(null);

  useEffect(() => {
    fetch(`${API_URL}/health`)
      .then(res => res.json())
      .then(data => {
        console.log('API Health:', data);
        setConnected(true);
      })
      .catch(err => {
        console.error('API connection failed:', err);
        setConnected(false);
      });
  }, []);

  useEffect(() => {
    const fetchInitialData = async () => {
      const data = await getLatestSensors();
      if (data && data.sensors) {
        setSensors(data.sensors);
        setLastUpdate(new Date().toLocaleTimeString());

        // Auto-select first equipment and sensor
        if (Object.keys(data.sensors).length > 0) {
          const firstSensorData = Object.values(data.sensors)[0];
          const firstEquipmentName = firstSensorData.equipment_name || firstSensorData.equipment_id;
          if (!selectedEquipment) {
            setSelectedEquipment(firstEquipmentName);
          }
          if (!selectedSensor) {
            setSelectedSensor(Object.keys(data.sensors)[0]);
          }
        }
      }
    };

    fetchInitialData();

    websocketService.connect(
      (data) => {
        if (data.type === 'sensor_update' && data.sensors) {
          setSensors(data.sensors);
          setLastUpdate(new Date().toLocaleTimeString());
          setWsConnected(true);
        }
      },
      (error) => {
        console.error('WebSocket error:', error);
        setWsConnected(false);
      }
    );

    return () => {
      websocketService.disconnect();
    };
  }, []);

  const groupByEquipment = () => {
    const groups = {};
    Object.entries(sensors).forEach(([sensorId, data]) => {
      const equipmentId = data.equipment_id;
      const equipmentKey = data.equipment_name || equipmentId; // Use name as key if available
      if (!groups[equipmentKey]) {
        groups[equipmentKey] = {
          equipmentId: equipmentId,
          equipmentName: data.equipment_name,
          sensors: []
        };
      }
      groups[equipmentKey].sensors.push({ id: sensorId, ...data });
    });
    return groups;
  };

  const equipmentGroups = groupByEquipment();
  const sensorCount = Object.keys(sensors).length;
  const equipmentCount = Object.keys(equipmentGroups).length;
  const sel = selectedSensor ? sensors[selectedSensor] : null;

  const selectEquipment = (key) => {
    setSelectedEquipment(key);
    const first = equipmentGroups[key]?.sensors?.[0];
    if (first) setSelectedSensor(first.id);
  };

  return (
    <AppShell user={user} title="Overview" wsConnected={wsConnected} lastUpdate={lastUpdate}>
      <div className="page-head">
        <div>
          <div className="eyebrow">Live telemetry</div>
          <h1>Overview</h1>
          <div className="sub">Real-time sensor stream across your monitored equipment.</div>
        </div>
        <span className={`badge ${wsConnected ? 'badge-live' : 'badge-warn'}`}>
          <span className={`sdot ${wsConnected ? 'ok' : 'pending'}`} />
          {wsConnected ? 'Streaming' : 'Connecting'}
        </span>
      </div>

      <div className="kpi-row" style={{ marginBottom: 'var(--gap)' }}>
        <div className="kpi">
          <div className="kpi-label">Active channels</div>
          <div className="kpi-value">{sensorCount}</div>
          <div className="kpi-foot">sensors reporting</div>
        </div>
        <div className="kpi">
          <div className="kpi-label">Equipment</div>
          <div className="kpi-value">{equipmentCount}</div>
          <div className="kpi-foot">monitored units</div>
        </div>
        <div className="kpi" style={{ '--accent': 'var(--live)' }}>
          <div className="kpi-label">Stream</div>
          <div className="kpi-value" style={{ fontSize: '22px' }}>{wsConnected ? 'LIVE' : '—'}</div>
          <div className="kpi-foot">{lastUpdate ? `updated ${lastUpdate}` : 'awaiting data'}</div>
        </div>
        <div className="kpi" style={{ '--accent': 'var(--signal)' }}>
          <div className="kpi-label">Selected channel</div>
          <div className="kpi-value">
            {sel && sel.value !== undefined ? sel.value.toFixed(2) : '—'}
            <span style={{ fontSize: '14px', color: 'var(--muted)', marginLeft: 6 }}>{sel?.unit || ''}</span>
          </div>
          <div className="kpi-foot">{sel ? (sel.sensor_name || selectedSensor) : 'none selected'}</div>
        </div>
      </div>

      <div className="dash-grid">
        <section className="panel" style={{ padding: 0 }}>
          <div className="panel-head">
            <h2>Real-time chart</h2>
            <div className="dash-selectors">
              <select value={selectedEquipment || ''} onChange={(e) => selectEquipment(e.target.value)}>
                <option value="">Equipment…</option>
                {Object.entries(equipmentGroups).map(([key, g]) => (
                  <option key={g.equipmentId || key} value={key}>{g.equipmentName || key}</option>
                ))}
              </select>
              <select value={selectedSensor || ''} onChange={(e) => setSelectedSensor(e.target.value)} disabled={!selectedEquipment}>
                <option value="">Sensor…</option>
                {selectedEquipment && equipmentGroups[selectedEquipment]?.sensors.map((s) => (
                  <option key={s.id} value={s.id}>{s.sensor_name || s.id}</option>
                ))}
              </select>
            </div>
          </div>
          <div style={{ padding: '18px' }}>
            {selectedSensor ? (
              <SensorChart
                sensorId={selectedSensor}
                title={`${sensors[selectedSensor]?.sensor_name || selectedSensor} — historical`}
              />
            ) : (
              <div className="dash-empty">
                <Icon name="activity" size={26} color="var(--faint)" />
                <p>Select equipment and a channel to plot its history.</p>
              </div>
            )}
          </div>
        </section>

        <aside className="panel" style={{ padding: 0, display: 'flex', flexDirection: 'column' }}>
          <div className="panel-head">
            <h2>Live readouts</h2>
            <span className="badge">{sensorCount}</span>
          </div>
          <div className="readout-list">
            {equipmentCount === 0 && <div className="dash-empty"><p>Waiting for sensor data…</p></div>}
            {Object.entries(equipmentGroups).map(([key, g]) => (
              <div className="readout-group" key={g.equipmentId || key}>
                <div className="readout-eq"><Icon name="box" size={13} /> {g.equipmentName || key}</div>
                {g.sensors.map((s) => (
                  <button
                    key={s.id}
                    className={`readout${selectedSensor === s.id ? ' active' : ''}`}
                    onClick={() => { setSelectedEquipment(key); setSelectedSensor(s.id); }}
                  >
                    <span className="readout-name">{s.sensor_name || s.id}</span>
                    <span className="readout-val mono">
                      {s.value !== undefined ? s.value.toFixed(2) : '—'}
                      <i>{s.unit || ''}</i>
                    </span>
                  </button>
                ))}
              </div>
            ))}
          </div>
        </aside>
      </div>
    </AppShell>
  );
}

function App() {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // The access token is an httpOnly cookie (not visible to JS); restore the session from
    // the saved (non-sensitive) user profile. An invalid/expired cookie surfaces as a 401
    // on the first API call, which the api client handles (refresh, else redirect to login).
    const savedUser = localStorage.getItem('user');
    if (savedUser) {
      setUser(JSON.parse(savedUser));
    }
    setLoading(false);
  }, []);

  const handleLogin = (userData) => {
    setUser(userData);
  };

  if (loading) {
    return <div style={{ 
      display: 'flex', 
      justifyContent: 'center', 
      alignItems: 'center', 
      height: '100vh',
      background: '#0a0e27',
      color: '#d1d4dc'
    }}>Loading...</div>;
  }

  return (
    <Router>
      <Routes>
        <Route path="/login" element={
          user ? <Navigate to="/" replace /> : <Login onLogin={handleLogin} />
        } />
        <Route path="/" element={
          <ProtectedRoute user={user}>
            <Dashboard user={user} />
          </ProtectedRoute>
        } />
        <Route path="/alerts" element={
          <ProtectedRoute user={user}>
            <AppShell user={user} title="Alerts"><AlertHistory /></AppShell>
          </ProtectedRoute>
        } />
        <Route path="/alert-rules" element={
          <ProtectedRoute user={user}>
            <AppShell user={user} title="Alert Rules"><AlertRules /></AppShell>
          </ProtectedRoute>
        } />
        <Route path="/equipment" element={
          <ProtectedRoute user={user}>
            <AppShell user={user} title="Equipment"><Equipment /></AppShell>
          </ProtectedRoute>
        } />
        <Route path="/ml-models" element={
          <ProtectedRoute user={user}>
            <AppShell user={user} title="Models"><MLModels /></AppShell>
          </ProtectedRoute>
        } />
        <Route path="/settings" element={
          <ProtectedRoute user={user}>
            <AppShell user={user} title="Settings"><Settings /></AppShell>
          </ProtectedRoute>
        } />
        <Route path="/admin/*" element={
          <ProtectedRoute user={user}>
            <AppShell user={user} title="Admin"><AdminPanel /></AppShell>
          </ProtectedRoute>
        } />
      </Routes>
    </Router>
  );
}

export default App;
