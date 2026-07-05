// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import './Mandala.css';

// The living mark: twelve sensor streams converging inward to a luminous gold core that breathes.
// 12 — a number of wholeness (the clock, the zodiac) ringing the centre. The mark made alive;
// reduced-motion collapses it to the still, converged state.
export default function Mandala({ size = 120, className = '' }) {
  const N = 12;
  const R = 154;   // ring radius (viewBox is 320, centred at 160)
  const CORE = 34; // where a stream meets the core
  const spokes = Array.from({ length: N }, (_, i) => (
    <g key={i} transform={`rotate(${(i * 360) / N})`}>
      <line className="mdl-stream" x1={R - 14} y1="0" x2={CORE} y2="0" />
      <circle className="mdl-node" cx={R} cy="0" r="5" />
      <circle
        className="mdl-pulse"
        cy="0"
        r="3.2"
        style={{ animationDelay: `${((i * 3.6) / N).toFixed(2)}s` }}
      />
    </g>
  ));
  return (
    <svg
      className={`mdl ${className}`.trim()}
      width={size}
      height={size}
      viewBox="0 0 320 320"
      role="img"
      aria-label="IndustryFlow"
    >
      <defs>
        <radialGradient id="mdlCore" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#fbf6ea" stopOpacity="1" />
          <stop offset="26%" stopColor="#e6c67c" stopOpacity="0.9" />
          <stop offset="62%" stopColor="#cfa94e" stopOpacity="0.28" />
          <stop offset="100%" stopColor="#cfa94e" stopOpacity="0" />
        </radialGradient>
      </defs>
      <g transform="translate(160 160)">
        <circle className="mdl-strata" r="154" strokeWidth="1.4" opacity="0.5" />
        <circle className="mdl-strata" r="120" strokeWidth="1" opacity="0.34" strokeDasharray="3 9" />
        <circle className="mdl-strata" r="86" strokeWidth="1.2" opacity="0.24" />
        {spokes}
        <circle className="mdl-coreglow" r="46" fill="url(#mdlCore)" />
        <circle r="7" fill="#f6efe0" />
      </g>
    </svg>
  );
}
