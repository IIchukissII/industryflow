// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import React, { useEffect, useRef, useState, useCallback } from 'react';
import { createChart, LineSeries, AreaSeries, CandlestickSeries } from 'lightweight-charts';
import { getMeasurements, getCombinedAggregations } from '../services/api';
import websocketService from '../services/websocket';
import ChartControls from './ChartControls';
import './SensorChart.css';

// One accessible series colour on the dark chart surface (#131722). A single series needs no
// legend box — the header names it — so this is the only categorical hue in play.
const SURFACE = '#131722';
const SERIES = '#4c9aff';
const UP = '#26a69a';
const DOWN = '#ef5350';

// Live tick appending only makes sense for the raw, single-value forms; aggregated buckets and
// candlestick OHLC are reloaded on a gentle timer instead of poked per reading.
const isRaw = (timeframe) => timeframe === 'raw';
const supportsLiveTick = (timeframe, chartType) =>
  isRaw(timeframe) && (chartType === 'line' || chartType === 'area');

const fmt = (v) =>
  v == null || Number.isNaN(v)
    ? '—'
    : Math.abs(v) >= 100 ? v.toFixed(1) : v.toFixed(3).replace(/0+$/, '').replace(/\.$/, '');

const fmtTime = (t) =>
  t == null ? '' : new Date(t * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });

const dedupeAscending = (rows) => {
  rows.sort((a, b) => a.time - b.time);
  const out = [];
  for (const r of rows) {
    if (out.length && out[out.length - 1].time === r.time) out[out.length - 1] = r; // last wins per ts
    else out.push(r);
  }
  return out;
};

const SensorChart = ({ sensorId }) => {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);
  const seriesKeyRef = useRef(null); // `${chartType}|${timeframe}` — series identity

  const [selectedTimeframe, setSelectedTimeframe] = useState('raw');
  const [selectedChartType, setSelectedChartType] = useState('line');
  const [selectedTimeRange, setSelectedTimeRange] = useState('100');

  const [status, setStatus] = useState('loading'); // loading | ready | empty | error
  const [meta, setMeta] = useState({ name: null, unit: '' });
  const [latest, setLatest] = useState(null); // { value, time } — most recent point
  const [hover, setHover] = useState(null);    // { value, time } under the crosshair
  const [live, setLive] = useState(false);     // a WS tick landed recently

  // effective chart type (candlestick needs OHLC, so it degrades to line on raw)
  const chartType =
    selectedChartType === 'candlestick' && isRaw(selectedTimeframe) ? 'line' : selectedChartType;

  // --- chart lifecycle: created ONCE, sized by a ResizeObserver ----------------------------
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const chart = createChart(el, {
      width: el.clientWidth,
      height: 380,
      layout: { background: { color: SURFACE }, textColor: '#a7abb6', fontSize: 11 },
      grid: { vertLines: { color: '#1c2030' }, horzLines: { color: '#1c2030' } },
      timeScale: { timeVisible: true, secondsVisible: true, borderColor: '#2a2e39' },
      rightPriceScale: { borderColor: '#2a2e39' },
      crosshair: {
        mode: 1,
        vertLine: { color: '#3a4256', width: 1, style: 3, labelBackgroundColor: '#2a2e39' },
        horzLine: { color: '#3a4256', width: 1, style: 3, labelBackgroundColor: '#2a2e39' },
      },
    });
    chartRef.current = chart;

    chart.subscribeCrosshairMove((param) => {
      const s = seriesRef.current;
      if (!param.time || !s || !param.seriesData.get(s)) {
        setHover(null);
        return;
      }
      const d = param.seriesData.get(s);
      const value = d.value != null ? d.value : d.close; // line/area vs candlestick
      setHover({ time: param.time, value });
    });

    const ro = new ResizeObserver(() => chart.applyOptions({ width: el.clientWidth }));
    ro.observe(el);

    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      seriesKeyRef.current = null;
    };
  }, []);

  // ensure the right series exists for the current type/timeframe; returns it
  const ensureSeries = useCallback((type) => {
    const chart = chartRef.current;
    if (!chart) return null;
    const key = `${type}|${selectedTimeframe}`;
    if (seriesRef.current && seriesKeyRef.current === key) return seriesRef.current;

    if (seriesRef.current) {
      try { chart.removeSeries(seriesRef.current); } catch { /* already gone */ }
      seriesRef.current = null;
    }

    // v5 replaces the per-type add*Series helpers with addSeries(SeriesDefinition, options).
    let series;
    if (type === 'area') {
      series = chart.addSeries(AreaSeries, {
        lineColor: SERIES, lineWidth: 2,
        topColor: 'rgba(76,154,255,0.28)', bottomColor: 'rgba(76,154,255,0.0)',
        priceLineVisible: false,
      });
    } else if (type === 'candlestick') {
      series = chart.addSeries(CandlestickSeries, {
        upColor: UP, downColor: DOWN, borderVisible: false, wickUpColor: UP, wickDownColor: DOWN,
      });
    } else {
      series = chart.addSeries(LineSeries, { color: SERIES, lineWidth: 2, priceLineVisible: false });
    }
    seriesRef.current = series;
    seriesKeyRef.current = key;
    return series;
  }, [selectedTimeframe]);

  // --- historical load: on sensor / timeframe / type / range change ------------------------
  useEffect(() => {
    if (!sensorId) return;
    let cancelled = false;

    const load = async () => {
      setStatus((s) => (s === 'ready' ? s : 'loading'));
      try {
        let data = [];
        if (isRaw(selectedTimeframe)) {
          const rows = await getMeasurements(sensorId, parseInt(selectedTimeRange, 10));
          data = dedupeAscending(
            (rows || []).map((r) => ({ time: new Date(r.time).getTime() / 1000, value: r.value }))
          );
        } else {
          const combined = await getCombinedAggregations(sensorId, parseInt(selectedTimeRange, 10));
          const agg = combined?.timeframes?.[selectedTimeframe]?.data || [];
          data = dedupeAscending(
            chartType === 'candlestick'
              ? agg.map((i) => ({
                  time: new Date(i.time).getTime() / 1000,
                  open: i.avg_value, high: i.max_value, low: i.min_value, close: i.avg_value,
                }))
              : agg.map((i) => ({ time: new Date(i.time).getTime() / 1000, value: i.avg_value }))
          );
        }
        if (cancelled) return;

        const series = ensureSeries(chartType);
        if (!series) return;
        series.setData(data);
        chartRef.current.timeScale().fitContent(); // fit ONLY on a query change, never per tick

        if (data.length) {
          const last = data[data.length - 1];
          setLatest({ value: last.value != null ? last.value : last.close, time: last.time });
          setStatus('ready');
        } else {
          setLatest(null);
          setStatus('empty');
        }
      } catch (err) {
        if (!cancelled) { console.error('Chart load failed:', err); setStatus('error'); }
      }
    };

    load();
    // aggregated views can't be poked per-tick; refresh them gently (no series churn, no re-fit)
    let timer = null;
    if (!supportsLiveTick(selectedTimeframe, chartType)) {
      timer = setInterval(load, 30000);
    }
    return () => { cancelled = true; if (timer) clearInterval(timer); };
  }, [sensorId, selectedTimeframe, chartType, selectedTimeRange, ensureSeries]);

  // --- live updates: append this sensor's WS ticks incrementally (no redraw) ----------------
  useEffect(() => {
    if (!sensorId) return;
    const unsub = websocketService.subscribe((msg) => {
      if (msg?.type !== 'sensor_update' || !msg.sensors) return;
      const entry = msg.sensors[sensorId];
      if (!entry) return;

      // name/unit come enriched on the stream; keep the header fresh + mark live
      setMeta((m) =>
        m.name === (entry.sensor_name || null) && m.unit === (entry.unit || '')
          ? m
          : { name: entry.sensor_name || m.name, unit: entry.unit || m.unit });
      setLive(true);

      if (entry.value == null || !supportsLiveTick(selectedTimeframe, chartType)) return;
      const point = { time: Math.floor(msg.timestamp), value: entry.value };
      const series = seriesRef.current;
      if (series) {
        try { series.update(point); } catch { /* out-of-order tick — ignore */ }
        setLatest(point);
      }
    });
    return unsub;
  }, [sensorId, selectedTimeframe, chartType]);

  // clear the "live" pulse if ticks stop arriving
  useEffect(() => {
    if (!live) return;
    const t = setTimeout(() => setLive(false), 5000);
    return () => clearTimeout(t);
  }, [live, latest]);

  if (!sensorId) {
    return <div className="sensor-chart__placeholder">Select a sensor to view its chart</div>;
  }

  const shown = hover || latest;
  const title = meta.name || `Sensor ${sensorId.slice(0, 8)}`;

  return (
    <div className="sensor-chart">
      <div className="sensor-chart__head">
        <div className="sensor-chart__id">
          <span className={`sensor-chart__dot ${live ? 'is-live' : ''}`} />
          <span className="sensor-chart__name" title={title}>{title}</span>
        </div>
        <div className="sensor-chart__readout">
          <span className="sensor-chart__value">{fmt(shown?.value)}</span>
          <span className="sensor-chart__unit">{meta.unit}</span>
          <span className="sensor-chart__ts">{shown?.time ? fmtTime(shown.time) : ''}</span>
        </div>
      </div>

      <ChartControls
        selectedTimeframe={selectedTimeframe}
        onTimeframeChange={setSelectedTimeframe}
        selectedChartType={selectedChartType}
        onChartTypeChange={setSelectedChartType}
        selectedTimeRange={selectedTimeRange}
        onTimeRangeChange={setSelectedTimeRange}
      />

      <div className="sensor-chart__canvas-wrap">
        <div ref={containerRef} className="sensor-chart__canvas" />
        {status !== 'ready' && (
          <div className="sensor-chart__overlay">
            {status === 'loading' && <span className="sensor-chart__spinner" aria-label="Loading" />}
            {status === 'empty' && <span>No data in this range yet</span>}
            {status === 'error' && <span className="sensor-chart__err">Couldn’t load chart data — retrying…</span>}
          </div>
        )}
      </div>
    </div>
  );
};

export default SensorChart;
