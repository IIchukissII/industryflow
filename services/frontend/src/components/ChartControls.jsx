// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import './ChartControls.css';

const TIMEFRAMES = [
  { value: 'raw', label: 'Raw' },
  { value: '1min', label: '1 minute' },
  { value: '5min', label: '5 minutes' },
  { value: '1hour', label: '1 hour' },
];

const CHART_TYPES = [
  { value: 'line', label: 'Line' },
  { value: 'area', label: 'Area' },
  { value: 'candlestick', label: 'Range' },
];

const TIME_RANGES = [
  { value: '100', label: '100' },
  { value: '200', label: '200' },
  { value: '500', label: '500' },
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

const ChartControls = ({
  selectedTimeframe,
  onTimeframeChange,
  selectedChartType,
  onChartTypeChange,
  selectedTimeRange,
  onTimeRangeChange,
}) => (
  <div className="chart-controls">
    <Seg label="Resolution" options={TIMEFRAMES} value={selectedTimeframe} onChange={onTimeframeChange} />
    <Seg
      label="Chart style" options={CHART_TYPES} value={selectedChartType} onChange={onChartTypeChange}
      disabledWhen={(v) => v === 'candlestick' && selectedTimeframe === 'raw'}
    />
    <div className="chart-controls-points">
      <Seg label="Points shown" options={TIME_RANGES} value={selectedTimeRange} onChange={onTimeRangeChange} />
      <span aria-hidden="true">points</span>
    </div>
  </div>
);

export default ChartControls;
