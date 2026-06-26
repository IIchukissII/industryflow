// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import React from 'react';
import './ChartControls.css';

const ChartControls = ({
  selectedTimeframe,
  onTimeframeChange,
  selectedChartType,
  onChartTypeChange,
  selectedTimeRange,
  onTimeRangeChange
}) => {
  const timeframes = [
    { value: 'raw', label: 'Raw Data' },
    { value: '1min', label: '1 Minute' },
    { value: '5min', label: '5 Minutes' },
    { value: '1hour', label: '1 Hour' }
  ];

  const chartTypes = [
    { value: 'line', label: 'Line' },
    { value: 'area', label: 'Area' },
    { value: 'candlestick', label: 'Candlestick' }
  ];

  const timeRanges = [
    { value: '100', label: 'Last 100' },
    { value: '200', label: 'Last 200' },
    { value: '500', label: 'Last 500' }
  ];

  return (
    <div className="chart-controls">
      <div className="control-group">
        <label>Timeframe:</label>
        <div className="button-group">
          {timeframes.map(tf => (
            <button
              key={tf.value}
              className={selectedTimeframe === tf.value ? 'active' : ''}
              onClick={() => onTimeframeChange(tf.value)}
            >
              {tf.label}
            </button>
          ))}
        </div>
      </div>

      <div className="control-group">
        <label>Chart Type:</label>
        <div className="button-group">
          {chartTypes.map(ct => (
            <button
              key={ct.value}
              className={selectedChartType === ct.value ? 'active' : ''}
              onClick={() => onChartTypeChange(ct.value)}
              disabled={selectedTimeframe === 'raw' && ct.value === 'candlestick'}
            >
              {ct.label}
            </button>
          ))}
        </div>
      </div>

      <div className="control-group">
        <label>Data Points:</label>
        <div className="button-group">
          {timeRanges.map(tr => (
            <button
              key={tr.value}
              className={selectedTimeRange === tr.value ? 'active' : ''}
              onClick={() => onTimeRangeChange(tr.value)}
            >
              {tr.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
};

export default ChartControls;