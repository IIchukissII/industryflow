// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import React, { useEffect, useRef, useState } from 'react';
import { createChart } from 'lightweight-charts';
import { getMeasurements, getCombinedAggregations } from '../services/api';
import ChartControls from './ChartControls';

const SensorChart = ({ sensorId }) => {
  const chartContainerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);

  const [selectedTimeframe, setSelectedTimeframe] = useState('raw');
  const [selectedChartType, setSelectedChartType] = useState('line');
  const [selectedTimeRange, setSelectedTimeRange] = useState('100');

  // Initialize chart
  useEffect(() => {
    if (!chartContainerRef.current) return;

    const chart = createChart(chartContainerRef.current, {
      width: chartContainerRef.current.clientWidth,
      height: 400,
      layout: {
        background: { color: '#131722' },
        textColor: '#d1d4dc',
      },
      grid: {
        vertLines: { color: '#1e222d' },
        horzLines: { color: '#1e222d' },
      },
      timeScale: {
        timeVisible: true,
        secondsVisible: true,
        borderColor: '#2a2e39',
      },
      rightPriceScale: {
        borderColor: '#2a2e39',
      },
    });

    chartRef.current = chart;

    // Handle window resize
    const handleResize = () => {
      if (chartContainerRef.current && chartRef.current) {
        chartRef.current.applyOptions({
          width: chartContainerRef.current.clientWidth,
        });
      }
    };

    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      if (chartRef.current) {
        chartRef.current.remove();
      }
    };
  }, []);

  // Create appropriate series based on chart type
 const createSeries = (chartType) => {
  if (!chartRef.current) return null;
  
  // Candlestick only works with aggregated data (needs OHLC)
  if (chartType === 'candlestick' && selectedTimeframe === 'raw') {
    chartType = 'line';
  }

  // Remove old series if exists
  if (seriesRef.current) {
    try {
      chartRef.current.removeSeries(seriesRef.current);
    } catch (error) {
      console.log('Could not remove series, creating new one');
    }
    seriesRef.current = null;
  }

  let series;
  const seriesOptions = {
    color: '#2962ff',
    lineWidth: 2,
  };

  switch (chartType) {
    case 'area':
      series = chartRef.current.addAreaSeries({
        ...seriesOptions,
        topColor: 'rgba(41, 98, 255, 0.4)',
        bottomColor: 'rgba(41, 98, 255, 0.0)',
      });
      break;
    case 'candlestick':
      series = chartRef.current.addCandlestickSeries({
        upColor: '#26a69a',
        downColor: '#ef5350',
        borderVisible: false,
        wickUpColor: '#26a69a',
        wickDownColor: '#ef5350',
      });
      break;
    case 'line':
    default:
      series = chartRef.current.addLineSeries(seriesOptions);
      break;
  }

  seriesRef.current = series;
  return series;
};

  // Fetch and update chart data
  useEffect(() => {
    const fetchData = async () => {
      try {
        const series = createSeries(selectedChartType);
        if (!series) return;

        let chartData = [];

        if (selectedTimeframe === 'raw') {
          // Fetch raw data
          const data = await getMeasurements(sensorId, parseInt(selectedTimeRange));

          chartData = data
            .map(item => ({
              time: new Date(item.time).getTime() / 1000,
              value: item.value,
            }))
            .sort((a, b) => a.time - b.time)
            .filter((item, index, array) => 
              index === 0 || item.time !== array[index - 1].time
            )
            .sort((a, b) => a.time - b.time);

        } else {
          // Fetch aggregated data
          const combined = await getCombinedAggregations(
            sensorId,
            parseInt(selectedTimeRange)
          );

          const aggregations = combined.timeframes[selectedTimeframe]?.data || [];

          if (selectedChartType === 'candlestick') {
            // Candlestick format: open, high, low, close
            const rawData = aggregations
              .map(item => ({
                time: new Date(item.time).getTime() / 1000,
                open: item.avg_value,
                high: item.max_value,
                low: item.min_value,
                close: item.avg_value,
              }))
              .sort((a, b) => a.time - b.time);

            // Remove duplicates - keep only first occurrence of each timestamp
            const seen = new Set();
            chartData = rawData.filter(item => {
              if (seen.has(item.time)) {
                return false;
              }
              seen.add(item.time);
              return true;
            });

          } else {
            // Line/Area format: use average value
            const rawData = aggregations
              .map(item => ({
                time: new Date(item.time).getTime() / 1000,
                value: item.avg_value,
              }))
              .sort((a, b) => a.time - b.time);

            // Remove duplicates - keep only first occurrence of each timestamp
            const seen = new Set();
            chartData = rawData.filter(item => {
              if (seen.has(item.time)) {
                return false;
              }
              seen.add(item.time);
              return true;
            });
          }
        }
        series.setData(chartData);
        chartRef.current.timeScale().fitContent();

      } catch (error) {
        console.error('Error fetching chart data:', error);
      }
    };

    if (sensorId) {
      fetchData();
      const interval = setInterval(fetchData, 10000); // Refresh every 10 seconds
      return () => clearInterval(interval);
    }
  }, [sensorId, selectedTimeframe, selectedChartType, selectedTimeRange]);

  if (!sensorId) {
    return (
      <div style={{
        padding: '40px',
        textAlign: 'center',
        color: '#787b86'
      }}>
        Select a sensor to view chart
      </div>
    );
  }

  return (
    <div>
      <ChartControls
        selectedTimeframe={selectedTimeframe}
        onTimeframeChange={setSelectedTimeframe}
        selectedChartType={selectedChartType}
        onChartTypeChange={setSelectedChartType}
        selectedTimeRange={selectedTimeRange}
        onTimeRangeChange={setSelectedTimeRange}
      />
      <div ref={chartContainerRef} />
    </div>
  );
};

export default SensorChart;