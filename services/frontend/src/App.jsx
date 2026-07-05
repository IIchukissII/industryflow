// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useState, useEffect, lazy, Suspense } from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import './App.css';
import { getLatestSensors } from './services/api';
import { API_URL } from './config';
import websocketService from './services/websocket';
import AppShell from './components/AppShell';
import ConvergenceCore from './components/ConvergenceCore';
import Icon from './components/Icon';

// Route pages + the chart are code-split: each becomes its own chunk, loaded on demand, so the
// initial bundle is the shell + dashboard skeleton rather than one big blob. lightweight-charts
// (the heaviest dep) ships only in the SensorChart chunk, fetched when a chart is first shown.
const SensorChart = lazy(() => import('./components/SensorChart'));
const AlertRules = lazy(() => import('./pages/AlertRules'));
const AlertHistory = lazy(() => import('./pages/AlertHistory'));
const AdminPanel = lazy(() => import('./pages/AdminPanel'));
const Equipment = lazy(() => import('./pages/Equipment'));
const Settings = lazy(() => import('./pages/Settings'));
const MLModels = lazy(() => import('./pages/MLModels'));
const Notebooks = lazy(() => import('./pages/Notebooks'));
const Help = lazy(() => import('./pages/Help'));
const Login = lazy(() => import('./pages/Login'));

function PageFallback() {
  return <div className="app-fallback"><span className="sdot pending" /> Loading…</div>;
}

function ProtectedRoute({ children, user }) {
  if (!user) {
    return <Navigate to="/login" replace />;
  }
  return children;
}

function Dashboard({ user }) {
  const [sensors, setSensors] = useState({});
  const [, setConnected] = useState(false);
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
    // Mount-only: seeds the initial equipment/sensor selection and opens the WS once.
    // selectedEquipment/selectedSensor are read only to pick defaults, deliberately not
    // a re-run trigger.
    // eslint-disable-next-line react-hooks/exhaustive-deps
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

  // Pin a channel from the Convergence: select it and sync the equipment so the history selectors follow.
  const selectSensor = (id) => {
    setSelectedSensor(id);
    const s = sensors[id];
    const key = s?.equipment_name || s?.equipment_id;
    if (key) setSelectedEquipment(key);
  };

  return (
    <AppShell user={user} title="Core" wsConnected={wsConnected} lastUpdate={lastUpdate}>
      <div className="page-head">
        <div>
          <div className="eyebrow">Live telemetry · every stream, converged</div>
          <h1>Core</h1>
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

      <div className="dash-core">
        <section className="panel conv-panel">
          <div className="panel-head">
            <h2>Convergence</h2>
            <span className={`badge ${wsConnected ? 'badge-live' : 'badge-warn'}`}>
              <span className={`sdot ${wsConnected ? 'ok' : 'pending'}`} />
              {sensorCount} live
            </span>
          </div>
          <div className="conv-wrap">
            <ConvergenceCore
              sensors={sensors}
              selectedSensor={selectedSensor}
              onSelect={selectSensor}
              wsConnected={wsConnected}
            />
          </div>
          <div className="conv-hint">Point at a node to read it · click to pin the channel</div>
        </section>

        <section className="panel" style={{ padding: 0 }}>
          <div className="panel-head">
            <h2>Channel history</h2>
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
              <Suspense fallback={<div className="dash-empty"><span className="sdot pending" /> Loading chart…</div>}>
                <SensorChart
                  sensorId={selectedSensor}
                  title={`${sensors[selectedSensor]?.sensor_name || selectedSensor} — historical`}
                />
              </Suspense>
            ) : (
              <div className="dash-empty">
                <Icon name="activity" size={26} color="var(--faint)" />
                <p>Point at a node in the Convergence, or pick a channel, to plot its history.</p>
              </div>
            )}
          </div>
        </section>
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
      <Suspense fallback={<PageFallback />}>
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
        <Route path="/notebooks" element={
          <ProtectedRoute user={user}>
            <Notebooks user={user} />
          </ProtectedRoute>
        } />
        <Route path="/help" element={
          <ProtectedRoute user={user}>
            <AppShell user={user} title="Help"><Help user={user} /></AppShell>
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
      </Suspense>
    </Router>
  );
}

export default App;
