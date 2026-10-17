// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useState, useEffect, lazy, Suspense } from 'react';
import { useNavigate } from 'react-router-dom';
import './Core.css';
import { getLatestSensors } from '../services/api';
import websocketService from '../services/websocket';
import AppShell from '../components/AppShell';
import ConvergenceCore from '../components/ConvergenceCore';
import authFetch from '../services/http';
import { coreReading } from '../utils/reading';

// lightweight-charts (the heaviest dependency) ships only in the chart's chunk.
const SensorChart = lazy(() => import('../components/SensorChart'));

const STATUS_RANK = { crit: 0, warn: 1 };

function fmtValue(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '—';
  const n = Number(v);
  return Math.abs(n) >= 1000 ? n.toFixed(0) : Math.abs(n) >= 100 ? n.toFixed(1) : n.toFixed(2);
}

// Every channel as a row: the ring's nodes, readable and selectable without hovering.
// Open alerts first, then by name; grouped by machine when there is more than one.
function ChannelList({ sensors, statusBySensor, selected, onSelect }) {
  const groups = new Map();
  Object.entries(sensors).forEach(([id, s]) => {
    const k = s.equipment_name || s.equipment_id || 'Other';
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push({ id, ...s });
  });
  const order = (a, b) =>
    (STATUS_RANK[statusBySensor[a.id]] ?? 2) - (STATUS_RANK[statusBySensor[b.id]] ?? 2)
    || (a.sensor_name || a.id).localeCompare(b.sensor_name || b.id);

  return (
    <div className="channels">
      {[...groups.entries()].map(([machine, list]) => (
        <div key={machine} className="channels-group">
          {groups.size > 1 && <h3 className="channels-machine">{machine}</h3>}
          <ul>
            {list.sort(order).map((c) => {
              const st = statusBySensor[c.id] || 'good';
              return (
                <li key={c.id}>
                  <button
                    type="button"
                    className={`channel st-${st}${c.id === selected ? ' sel' : ''}`}
                    aria-pressed={c.id === selected}
                    onClick={() => onSelect(c.id)}
                  >
                    <span className="channel-dot" aria-hidden="true" />
                    <span className="channel-name">
                      {c.sensor_name || c.id}
                      {st === 'crit' && <span className="sr-only"> (critical alert)</span>}
                      {st === 'warn' && <span className="sr-only"> (needs a look)</span>}
                    </span>
                    <span className="channel-value mono">
                      {fmtValue(c.value)} <span className="channel-unit">{c.unit || ''}</span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </div>
  );
}

export default function Core({ user }) {
  const [sensors, setSensors] = useState({});
  const [wsConnected, setWsConnected] = useState(false);
  const [lastUpdate, setLastUpdate] = useState(null);
  const [selectedSensor, setSelectedSensor] = useState(null);
  const [alertStatus, setAlertStatus] = useState({}); // sensor_id -> 'crit' | 'warn', from open alerts
  const navigate = useNavigate();

  useEffect(() => {
    const fetchInitialData = async () => {
      const data = await getLatestSensors();
      if (data && data.sensors) {
        setSensors(data.sensors);
        setLastUpdate(new Date().toLocaleTimeString());
        const first = Object.keys(data.sensors)[0];
        if (first) setSelectedSensor((cur) => cur || first);
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
    return () => websocketService.disconnect();
  }, []);

  // Open alerts reduced to a per-sensor health: critical → crit, high/medium → warn, worst wins.
  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const res = await authFetch('/api/alerts?acknowledged=false&limit=500');
        if (!res.ok) return;
        const data = await res.json();
        const map = {};
        (data || []).forEach((a) => {
          if (!a.sensor_id) return;
          const sev = (a.severity || '').toLowerCase();
          const st = sev === 'critical' ? 'crit'
            : (sev === 'high' || sev === 'medium' || sev === 'warning') ? 'warn' : null;
          if (!st) return;
          if (st === 'crit' || map[a.sensor_id] !== 'crit') map[a.sensor_id] = st;
        });
        if (active) setAlertStatus(map);
      } catch { /* best-effort; the ring shows healthy tints */ }
    };
    load();
    const iv = setInterval(load, 20000);
    return () => { active = false; clearInterval(iv); };
  }, []);

  const hasSensors = Object.keys(sensors).length > 0;

  return (
    <AppShell user={user} title="Core" wsConnected={wsConnected} lastUpdate={lastUpdate}>
      <div className="core">
        <p className="reading core-reading">
          {coreReading({ sensors, statusBySensor: alertStatus, wsConnected: wsConnected || !hasSensors })}
        </p>

        <div className="core-grid">
          <section className="core-ring" aria-label="Convergence: every channel on the ring">
            <ConvergenceCore
              sensors={sensors}
              selectedSensor={selectedSensor}
              onSelect={setSelectedSensor}
              onOpenAlerts={(id) => navigate(`/alerts?sensor=${encodeURIComponent(id)}`)}
              wsConnected={wsConnected}
              statusBySensor={alertStatus}
            />
          </section>

          <div className="core-side">
            {selectedSensor && (
              <section className="core-trace" aria-label="History of the selected channel">
                <Suspense fallback={<div className="core-wait"><span className="sdot pending" /> Loading the chart</div>}>
                  <SensorChart
                    sensorId={selectedSensor}
                    name={sensors[selectedSensor]?.sensor_name}
                    unit={sensors[selectedSensor]?.unit}
                  />
                </Suspense>
              </section>
            )}

            {hasSensors && (
              <section className="core-channels" aria-labelledby="channels-h">
                <h2 id="channels-h">Channels</h2>
                <ChannelList
                  sensors={sensors}
                  statusBySensor={alertStatus}
                  selected={selectedSensor}
                  onSelect={setSelectedSensor}
                />
              </section>
            )}
          </div>
        </div>
      </div>
    </AppShell>
  );
}
