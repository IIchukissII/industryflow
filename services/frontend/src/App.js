import React, { useState, useEffect } from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import './App.css';
import { getLatestSensors } from './services/api';
import websocketService from './services/websocket';
import SensorChart from './components/SensorChart';
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
  const [selectedSensor, setSelectedSensor] = useState(null);

  useEffect(() => {
    fetch('http://localhost:8000/health')
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

        if (!selectedSensor && Object.keys(data.sensors).length > 0) {
          setSelectedSensor(Object.keys(data.sensors)[0]);
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
      if (!groups[equipmentId]) {
        groups[equipmentId] = [];
      }
      groups[equipmentId].push({ id: sensorId, ...data });
    });
    return groups;
  };

  const equipmentGroups = groupByEquipment();

  const handleLogout = () => {
    localStorage.removeItem('access_token');
    localStorage.removeItem('user');
    window.location.href = '/login';
  };

  const linkStyle = {
    color: '#787b86',
    textDecoration: 'none',
    fontSize: '14px',
    padding: '8px 16px',
    borderRadius: '4px',
    border: '1px solid #2a2e39',
    transition: 'all 0.2s'
  };

  return (
    <div className="App">
      <header className="App-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: '30px' }}>
          <h1>IndustryFlow</h1>
          <a href="/" style={linkStyle}
            onMouseOver={(e) => {
              e.target.style.color = '#2962ff';
              e.target.style.borderColor = '#2962ff';
            }}
            onMouseOut={(e) => {
              e.target.style.color = '#787b86';
              e.target.style.borderColor = '#2a2e39';
            }}>
            Dashboard
          </a>
          <a href="/alerts" style={linkStyle}
            onMouseOver={(e) => {
              e.target.style.color = '#2962ff';
              e.target.style.borderColor = '#2962ff';
            }}
            onMouseOut={(e) => {
              e.target.style.color = '#787b86';
              e.target.style.borderColor = '#2a2e39';
            }}>
            Alerts
          </a>
          <a href="/alert-rules" style={linkStyle}
            onMouseOver={(e) => {
              e.target.style.color = '#2962ff';
              e.target.style.borderColor = '#2962ff';
            }}
            onMouseOut={(e) => {
              e.target.style.color = '#787b86';
              e.target.style.borderColor = '#2a2e39';
            }}>
            Alert Rules
          </a>
          <a href="/equipment" style={linkStyle}
            onMouseOver={(e) => {
              e.target.style.color = '#2962ff';
              e.target.style.borderColor = '#2962ff';
            }}
            onMouseOut={(e) => {
              e.target.style.color = '#787b86';
              e.target.style.borderColor = '#2a2e39';
            }}>
            Equipment
          </a>
          <a href="/ml-models" style={linkStyle}
            onMouseOver={(e) => {
              e.target.style.color = '#2962ff';
              e.target.style.borderColor = '#2962ff';
            }}
            onMouseOut={(e) => {
              e.target.style.color = '#787b86';
              e.target.style.borderColor = '#2a2e39';
            }}>
            ML Models
          </a>
          {user && user.is_superuser && (
            <a href="/admin" style={linkStyle}
              onMouseOver={(e) => {
                e.target.style.color = '#2962ff';
                e.target.style.borderColor = '#2962ff';
              }}
              onMouseOut={(e) => {
                e.target.style.color = '#787b86';
                e.target.style.borderColor = '#2a2e39';
              }}>
              ⚙️ Admin
            </a>
          )}
        </div>
        <div className="status">
          <span style={{ marginRight: '20px', color: '#d1d4dc' }}>
            👤 {user ? user.email : 'Guest'}
          </span>
          <a href="/settings" style={{
            ...linkStyle,
            marginRight: '15px',
            padding: '6px 12px'
          }}
            onMouseOver={(e) => {
              e.target.style.color = '#2962ff';
              e.target.style.borderColor = '#2962ff';
            }}
            onMouseOut={(e) => {
              e.target.style.color = '#787b86';
              e.target.style.borderColor = '#2a2e39';
            }}>
            ⚙️ Settings
          </a>
          API: <span className={connected ? 'connected' : 'disconnected'}>
            {connected ? '● Connected' : '● Disconnected'}
          </span>
          <span style={{ marginLeft: '15px' }}>
            WebSocket: <span className={wsConnected ? 'connected' : 'disconnected'}>
              {wsConnected ? '● Live' : '● Connecting'}
            </span>
          </span>
          {lastUpdate && <span style={{ marginLeft: '20px', color: '#787b86' }}>
            Last update: {lastUpdate}
          </span>}
          <button
            onClick={handleLogout}
            style={{
              marginLeft: '20px',
              background: 'transparent',
              border: '1px solid #2a2e39',
              color: '#787b86',
              padding: '6px 12px',
              borderRadius: '4px',
              cursor: 'pointer',
              fontSize: '13px',
              transition: 'all 0.2s'
            }}
            onMouseOver={(e) => {
              e.target.style.borderColor = '#ef5350';
              e.target.style.color = '#ef5350';
            }}
            onMouseOut={(e) => {
              e.target.style.borderColor = '#2a2e39';
              e.target.style.color = '#787b86';
            }}
          >
            Logout
          </button>
        </div>
      </header>

      <main className="App-main">
        <div className="dashboard-grid">
          {Object.entries(equipmentGroups).map(([equipmentId, sensorList]) => (
            <div className="card" key={equipmentId}>
              <h2>{equipmentId.replace(/_/g, ' ').toUpperCase()}</h2>
              <div className="sensor-list">
                {sensorList.map(sensor => (
                  <div
                    className={`sensor-item ${selectedSensor === sensor.id ? 'selected' : ''}`}
                    key={sensor.id}
                    onClick={() => setSelectedSensor(sensor.id)}
                    style={{ cursor: 'pointer' }}
                  >
                    <span className="sensor-name">
                      {sensor.id.replace(sensor.equipment_id, '').replace(/_/g, ' ').trim()}
                    </span>
                    <span className="sensor-value">
                      {sensor.value !== undefined ? sensor.value.toFixed(2) : 'N/A'} {sensor.unit || ''}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          ))}

          <div className="card wide">
            <h2>Real-time Chart</h2>
            {selectedSensor && sensors[selectedSensor] && (
              <div style={{ marginBottom: '10px', color: '#787b86', fontSize: '13px' }}>
                Viewing: <span style={{ color: '#2962ff', fontWeight: 'bold' }}>
                  {selectedSensor}
                </span> ({sensors[selectedSensor].equipment_id})
              </div>
            )}
            {selectedSensor ? (
              <SensorChart
                sensorId={selectedSensor}
                title={`${selectedSensor} - Historical Data`}
              />
            ) : (
              <p style={{ color: '#787b86' }}>Click on a sensor to view its chart</p>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}

function App() {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = localStorage.getItem('access_token');
    const savedUser = localStorage.getItem('user');
    
    if (token && savedUser) {
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
            <AlertHistory />
          </ProtectedRoute>
        } />
        <Route path="/alert-rules" element={
          <ProtectedRoute user={user}>
            <AlertRules />
          </ProtectedRoute>
        } />
        <Route path="/equipment" element={
          <ProtectedRoute user={user}>
            <Equipment />
          </ProtectedRoute>
        } />
        <Route path="/ml-models" element={
          <ProtectedRoute user={user}>
            <MLModels />
          </ProtectedRoute>
        } />
        <Route path="/settings" element={
          <ProtectedRoute user={user}>
            <Settings />
          </ProtectedRoute>
        } />
        <Route path="/admin/*" element={
          <ProtectedRoute user={user}>
            <AdminPanel />
          </ProtectedRoute>
        } />
      </Routes>
    </Router>
  );
}

export default App;
