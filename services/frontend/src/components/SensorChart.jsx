// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useEffect, useRef, useState, useCallback } from 'react';
import { createChart, LineSeries, AreaSeries, CandlestickSeries } from 'lightweight-charts';
import { getMeasurements, getReadingsInWindow, getAggregatesInWindow } from '../services/api';
import websocketService from '../services/websocket';
import ChartControls from './ChartControls';
import {
  dedupeAscending, toSecondPoints, windowOf, resolutionFor, resolutionFits, POINTS_PER_REQUEST,
} from '../utils/series';
import { sensorLabel } from '../utils/sensors';
import './SensorChart.css';

// The chart is drawn on the page ground itself, not in a box of its own. A single series needs
// no legend — the heading names it — so the gold of the selected channel is the only hue.
const SURFACE = 'rgba(0, 0, 0, 0)';
const SERIES = '#cfa94e';
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

// The axis and the crosshair in the viewer's local time, as the readout above them; the chart's own
// default is UTC. Tick types: 0 year, 1 month, 2 day of month, 3 time, 4 time with seconds.
const local = (t) => new Date(t * 1000);
const tickMark = (t, type) => {
  const d = local(t);
  if (type === 0) return String(d.getFullYear());
  if (type === 1) return d.toLocaleDateString([], { month: 'short' });
  if (type === 2) return d.toLocaleDateString([], { day: 'numeric', month: 'short' });
  // 24-hour ticks: a 12-hour label with seconds is wider than the space between ticks.
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false, ...(type === 4 ? { second: '2-digit' } : {}) });
};
const crosshairTime = (t) =>
  local(t).toLocaleString([], { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit' });

const fmtWhen = (ms) => new Date(ms).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
const count = (n) => n.toLocaleString();

// How often the drawn window is read again, so it keeps ending now; raw readings also arrive live.
const RELOAD_MS = 30000;


const SensorChart = ({ sensorId, name, unit }) => {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);
  const seriesKeyRef = useRef(null); // `${chartType}|${resolution}` — series identity

  const [selectedWindow, setSelectedWindow] = useState('1h');
  const [selectedResolution, setSelectedResolution] = useState('auto');
  const [selectedChartType, setSelectedChartType] = useState('line');
  // What the last load drew: the resolution (auto, or a fallback from raw) and why, if it differs
  // from what was asked; and, for an empty window, when the sensor's latest reading arrived.
  const [drawn, setDrawn] = useState(() => resolutionFor('auto', '1h'));
  const [notice, setNotice] = useState(null);
  const [lastSeen, setLastSeen] = useState(null);

  const [status, setStatus] = useState('loading'); // loading | ready | empty | error
  const [meta, setMeta] = useState({ name: null, unit: '' });
  const [latest, setLatest] = useState(null); // { value, time } — most recent point
  const [hover, setHover] = useState(null);    // { value, time } under the crosshair
  const [live, setLive] = useState(false);     // a WS tick landed recently

  // effective chart type (candlestick needs OHLC, so it degrades to line on raw)
  const typeFor = (resolution) =>
    selectedChartType === 'candlestick' && isRaw(resolution) ? 'line' : selectedChartType;
  const chartType = typeFor(drawn);

  // --- chart lifecycle: created ONCE, sized by a ResizeObserver ----------------------------
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const chart = createChart(el, {
      width: el.clientWidth,
      height: 300,
      layout: { background: { color: SURFACE }, textColor: '#9aa0ac', fontSize: 11, fontFamily: "'IBM Plex Mono', monospace" },
      grid: { vertLines: { visible: false }, horzLines: { color: '#161c2c' } },
      timeScale: { timeVisible: true, secondsVisible: true, borderVisible: false, tickMarkFormatter: tickMark },
      localization: { timeFormatter: crosshairTime },
      rightPriceScale: { borderVisible: false },
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

  // ensure the right series exists for the type and resolution; returns it
  const ensureSeries = useCallback((type, resolution) => {
    const chart = chartRef.current;
    if (!chart) return null;
    const key = `${type}|${resolution}`;
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
        topColor: 'rgba(207,169,78,0.26)', bottomColor: 'rgba(207,169,78,0.0)',
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
  }, []);

  // --- the window's load: on sensor / window / resolution / type change, then every RELOAD_MS ----
  useEffect(() => {
    if (!sensorId) return;
    let cancelled = false;
    const win = windowOf(selectedWindow);
    const typeOf = (resolution) =>
      selectedChartType === 'candlestick' && isRaw(resolution) ? 'line' : selectedChartType;

    const load = async (fit) => {
      setStatus((s) => (s === 'ready' ? s : 'loading'));
      try {
        const end = new Date();
        const start = new Date(end.getTime() - win.seconds * 1000);
        let resolution = resolutionFor(selectedResolution, selectedWindow);
        let data = [];
        let note = null;

        if (isRaw(resolution)) {
          const rows = await getReadingsInWindow(sensorId, start, end, POINTS_PER_REQUEST);
          if (rows === null) throw new Error('readings did not load');
          if (rows.length >= POINTS_PER_REQUEST) {
            // More readings than one request returns: raw would show only the newest slice of the window.
            if (selectedResolution === 'auto' && resolutionFits('1min', win.seconds)) {
              resolution = '1min';
              note = `More than ${count(POINTS_PER_REQUEST)} readings in this window: showing 1-minute averages.`;
            } else {
              note = `More than ${count(POINTS_PER_REQUEST)} readings in this window: showing the newest ${count(POINTS_PER_REQUEST)}.`;
            }
          }
          if (isRaw(resolution)) {
            const { points, merged } = toSecondPoints(rows);
            data = points;
            if (!note && merged) note = `${count(merged)} readings share a second with a later one: each second shows its last.`;
          }
        }
        if (!isRaw(resolution)) {
          const agg = await getAggregatesInWindow(sensorId, resolution, start, end, POINTS_PER_REQUEST);
          if (agg === null) throw new Error('aggregates did not load');
          const at = (i) => Math.floor(new Date(i.time).getTime() / 1000);
          data = dedupeAscending(
            typeOf(resolution) === 'candlestick'
              ? agg.map((i) => ({ time: at(i), open: i.avg_value, high: i.max_value, low: i.min_value, close: i.avg_value }))
              : agg.map((i) => ({ time: at(i), value: i.avg_value }))
          );
        }
        if (cancelled) return;

        const series = ensureSeries(typeOf(resolution), resolution);
        if (!series) return;
        series.setData(data);
        if (fit) chartRef.current.timeScale().fitContent(); // fit on a query change, never on a reload
        setDrawn(resolution);
        setNotice(note);

        if (data.length) {
          // The readout is the sensor's latest reading, never an average: an aggregate view reads it.
          const newest = isRaw(resolution) ? null : await getMeasurements(sensorId, 1);
          if (cancelled) return;
          const last = data[data.length - 1];
          setLatest(newest?.length
            ? { value: newest[0].value, time: Math.floor(new Date(newest[0].time).getTime() / 1000) }
            : { value: last.value, time: last.time });
          setLastSeen(null);
          setStatus('ready');
        } else {
          // An empty window says when the sensor was last heard from, not only that it is empty.
          const newest = await getMeasurements(sensorId, 1);
          if (cancelled) return;
          setLatest(null);
          setLastSeen(newest?.length ? new Date(newest[0].time).getTime() : null);
          setStatus('empty');
        }
      } catch (err) {
        if (!cancelled) { console.error('Chart load failed:', err); setStatus('error'); }
      }
    };

    load(true);
    const timer = setInterval(() => load(false), RELOAD_MS);
    return () => { cancelled = true; clearInterval(timer); };
  }, [sensorId, selectedWindow, selectedResolution, selectedChartType, ensureSeries]);

  // --- live updates: append this sensor's WS ticks incrementally (no redraw) ----------------
  useEffect(() => {
    if (!sensorId) return;
    const unsub = websocketService.subscribe((msg) => {
      if (msg?.type !== 'sensor_update' || !msg.sensors) return;
      const entry = msg.sensors[sensorId];
      if (!entry) return;

      // name/unit come enriched on the stream; keep the header fresh + mark live
      const label = sensorLabel(entry) || null;
      setMeta((m) =>
        m.name === label && m.unit === (entry.unit || '')
          ? m
          : { name: label || m.name, unit: entry.unit || m.unit });
      setLive(true);

      if (entry.value == null) return;
      // Whole seconds, as the loaded points: a second's later reading replaces its point.
      const point = { time: Math.floor(msg.timestamp), value: entry.value };
      setLatest(point);
      const series = seriesRef.current;
      if (series && supportsLiveTick(drawn, chartType)) {
        try { series.update(point); } catch { /* out-of-order tick — ignore */ }
      }
    });
    return unsub;
  }, [sensorId, drawn, chartType]);

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
  const title = meta.name || name || `Sensor ${sensorId.slice(0, 8)}`;

  return (
    <div className="sensor-chart">
      <div className="sensor-chart__head">
        <div className="sensor-chart__id">
          <span className={`sensor-chart__dot ${live ? 'is-live' : ''}`} aria-hidden="true" />
          <h2 className="sensor-chart__name" title={title}>{title}</h2>
        </div>
        <div className="sensor-chart__readout">
          <span className="sensor-chart__value">{fmt(shown?.value)}</span>
          <span className="sensor-chart__unit">{meta.unit || unit}</span>
          <span className="sensor-chart__ts">{shown?.time ? fmtTime(shown.time) : ''}</span>
        </div>
      </div>

      <ChartControls
        selectedWindow={selectedWindow}
        onWindowChange={setSelectedWindow}
        selectedResolution={selectedResolution}
        onResolutionChange={setSelectedResolution}
        drawnResolution={drawn}
        selectedChartType={selectedChartType}
        onChartTypeChange={setSelectedChartType}
      />
      {notice && <p className="sensor-chart__notice" role="status">{notice}</p>}

      <div className="sensor-chart__canvas-wrap">
        <div ref={containerRef} className="sensor-chart__canvas" />
        {status !== 'ready' && (
          <div className="sensor-chart__overlay">
            {status === 'loading' && <span className="sensor-chart__spinner" aria-label="Loading" />}
            {status === 'empty' && (
              <span>
                No readings in the last {windowOf(selectedWindow).label}.
                {lastSeen ? ` The latest arrived ${fmtWhen(lastSeen)}.` : ' This sensor has sent none yet.'}
              </span>
            )}
            {status === 'error' && <span className="sensor-chart__err">The chart could not load its data.</span>}
          </div>
        )}
      </div>
    </div>
  );
};

export default SensorChart;
