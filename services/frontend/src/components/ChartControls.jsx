// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { WINDOWS, windowOf, resolutionFits } from '../utils/series';
import './ChartControls.css';

const RESOLUTIONS = [
  { value: 'auto', label: 'Auto' },
  { value: 'raw', label: 'Raw' },
  { value: '1min', label: '1 minute' },
  { value: '5min', label: '5 minutes' },
  { value: '1hour', label: '1 hour' },
];

const RESOLUTION_LABEL = { raw: 'raw readings', '1min': '1-minute averages', '5min': '5-minute averages', '1hour': 'hourly averages' };

const CHART_TYPES = [
  { value: 'line', label: 'Line' },
  { value: 'area', label: 'Area' },
  { value: 'candlestick', label: 'Range' },
];

function Seg({ label, options, value, onChange, disabledWhen }) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          aria-pressed={value === o.value}
          disabled={disabledWhen ? disabledWhen(o.value) : false}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

// A window ending now, the resolution it is drawn at (Auto follows the window; a resolution with more
// buckets in the window than one request returns is not offered), and the chart's style.
const ChartControls = ({
  selectedWindow,
  onWindowChange,
  selectedResolution,
  onResolutionChange,
  drawnResolution,
  selectedChartType,
  onChartTypeChange,
}) => {
  const seconds = windowOf(selectedWindow).seconds;
  const change = (w) => {
    onWindowChange(w);
    // A resolution the new window cannot draw falls back to Auto.
    if (selectedResolution !== 'auto' && !resolutionFits(selectedResolution, windowOf(w).seconds)) onResolutionChange('auto');
  };
  return (
    <div className="chart-controls">
      <Seg label="Window" options={WINDOWS} value={selectedWindow} onChange={change} />
      <Seg
        label="Resolution" options={RESOLUTIONS} value={selectedResolution} onChange={onResolutionChange}
        disabledWhen={(v) => v !== 'auto' && !resolutionFits(v, seconds)}
      />
      <Seg
        label="Chart style" options={CHART_TYPES} value={selectedChartType} onChange={onChartTypeChange}
        disabledWhen={(v) => v === 'candlestick' && drawnResolution === 'raw'}
      />
      <span className="chart-controls-drawn" aria-live="polite">{RESOLUTION_LABEL[drawnResolution]}</span>
    </div>
  );
};

export default ChartControls;
