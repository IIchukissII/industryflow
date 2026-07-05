// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useEffect, useMemo, useRef, useState } from 'react';
import './ConvergenceCore.css';

// The Core, alive with real data: every reporting sensor is a node on the ring, grouped by
// equipment; each new reading sends a gold pulse converging inward to the luminous centre.
// Point at any node (or select it) and the core shows its live value — the many resolved to one.
const VB = 620;
const C = VB / 2;
const R_NODE = 250; // ring radius
const R_CORE = 72; // core radius

function fmt(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '—';
  const n = Number(v);
  return Math.abs(n) >= 1000 ? n.toFixed(0) : n.toFixed(2);
}

const STATUS_LABEL = { warn: 'Warning', crit: 'Critical' };

// A node's health: an active alert on that sensor (crit/warn), else "good" if it's reporting.
function statusOf(id, sensors, statusBySensor) {
  return statusBySensor[id] || (sensors[id] && sensors[id].value != null ? 'good' : null);
}

export default function ConvergenceCore({ sensors, selectedSensor, onSelect, wsConnected, statusBySensor = {} }) {
  const [hovered, setHovered] = useState(null);
  const [pulses, setPulses] = useState([]);
  const prev = useRef({});
  const seq = useRef(0);

  // Positions: sensors placed around the ring, grouped by equipment with a small gap per group.
  const { nodes, byId } = useMemo(() => {
    const entries = Object.entries(sensors || {});
    const groups = new Map();
    entries.forEach(([id, s]) => {
      const k = s.equipment_name || s.equipment_id || 'other';
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k).push([id, s]);
    });
    const total = entries.length;
    const out = [];
    const map = {};
    if (total) {
      const gap = Math.min(8, 90 / total); // degrees between groups
      const per = (360 - gap * groups.size) / total;
      let angle = -90;
      groups.forEach((list) => {
        list.forEach(([id, s]) => {
          const node = { id, s, theta: angle + per / 2 };
          out.push(node);
          map[id] = node;
          angle += per;
        });
        angle += gap;
      });
    }
    return { nodes: out, byId: map };
  }, [sensors]);

  // Emit an inward pulse on every stream whose value changed since the last frame of data.
  useEffect(() => {
    const fresh = [];
    Object.entries(sensors || {}).forEach(([id, s]) => {
      if (prev.current[id] !== undefined && prev.current[id] !== s.value && byId[id]) {
        seq.current += 1;
        fresh.push({ key: `${id}-${seq.current}`, theta: byId[id].theta });
      }
      prev.current[id] = s.value;
    });
    if (fresh.length) setPulses((p) => [...p.slice(-80), ...fresh]);
  }, [sensors, byId]);

  const removePulse = (key) => setPulses((p) => p.filter((x) => x.key !== key));

  // The core reads whatever you point at, else the current selection, else the whole field.
  const focusId = hovered || selectedSensor;
  const focus = focusId ? sensors[focusId] : null;
  const focusStatus = focusId ? statusOf(focusId, sensors, statusBySensor) : null;
  const count = nodes.length;

  return (
    <div className="conv">
      <svg className="conv-svg" viewBox={`0 0 ${VB} ${VB}`} role="img"
           aria-label="Live sensor streams converging to the core">
        <defs>
          <radialGradient id="convCore" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#fbf6ea" stopOpacity="0.9" />
            <stop offset="30%" stopColor="#e6c67c" stopOpacity="0.55" />
            <stop offset="70%" stopColor="#cfa94e" stopOpacity="0.14" />
            <stop offset="100%" stopColor="#cfa94e" stopOpacity="0" />
          </radialGradient>
        </defs>
        <g transform={`translate(${C} ${C})`}>
          <circle className="conv-strata" r={R_NODE} strokeWidth="1.3" opacity="0.4" />
          <circle className="conv-strata" r="180" strokeWidth="1" opacity="0.26" strokeDasharray="3 10" />
          <circle className="conv-strata" r="120" strokeWidth="1.2" opacity="0.18" />

          {nodes.map((n) => {
            const on = n.id === focusId;
            return (
              <g key={n.id} transform={`rotate(${n.theta})`}>
                <line className={`conv-stream${on ? ' on' : ''}`} x1={R_NODE - 8} y1="0" x2={R_CORE + 6} y2="0" />
              </g>
            );
          })}

          {/* pulses ride the streams inward; each removes itself when it reaches the core */}
          {pulses.map((p) => (
            <g key={p.key} transform={`rotate(${p.theta})`}>
              <circle className="conv-pulse" cy="0" r="3.4" onAnimationEnd={() => removePulse(p.key)} />
            </g>
          ))}

          {nodes.map((n) => {
            const on = n.id === focusId;
            const st = statusOf(n.id, sensors, statusBySensor);
            return (
              <g key={n.id} transform={`rotate(${n.theta})`}>
                <circle
                  className={`conv-node${st ? ` st-${st}` : ''}${on ? ' on' : ''}${n.id === selectedSensor ? ' sel' : ''}`}
                  cx={R_NODE} cy="0" r={on ? 8 : 5.5}
                  onMouseEnter={() => setHovered(n.id)}
                  onMouseLeave={() => setHovered((h) => (h === n.id ? null : h))}
                  onClick={() => onSelect(n.id)}
                >
                  <title>{`${n.s.sensor_name || n.id}${st && st !== 'good' ? ` — ${STATUS_LABEL[st]}` : ''}`}</title>
                </circle>
              </g>
            );
          })}

          <circle className="conv-coreglow" r="96" fill="url(#convCore)" />
          <circle className="conv-coredot" r="6" />
        </g>
      </svg>

      {/* the core readout — crisp HTML over the svg */}
      <div className="conv-core">
        {focus ? (
          <>
            <div className="conv-core-val">{fmt(focus.value)}<span className="conv-core-unit">{focus.unit || ''}</span></div>
            <div className="conv-core-name">{focus.sensor_name || focusId}</div>
            <div className="conv-core-eq">{focus.equipment_name || ''}</div>
            {focusStatus && focusStatus !== 'good' && (
              <div className={`conv-core-pill st-${focusStatus}`}>{STATUS_LABEL[focusStatus]}</div>
            )}
          </>
        ) : (
          <>
            <div className="conv-core-val">{count}</div>
            <div className="conv-core-name">{count === 1 ? 'channel' : 'channels'} converging</div>
            <div className="conv-core-eq">{wsConnected ? 'live' : 'awaiting stream'}</div>
          </>
        )}
      </div>

      {count === 0 && <div className="conv-empty">Waiting for sensor data…</div>}
    </div>
  );
}
