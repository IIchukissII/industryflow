// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import axios from 'axios';
import { API_URL as API_BASE_URL } from '../config';
import { csrfHeaders, endSession, refreshSession, shouldRefresh } from './session';

export { getCookie } from './session';

// Authentication is cookie-based (ADR-0004 dec 3): the access token lives in an httpOnly
// cookie the browser sends automatically, so we never read or store it in JS. We only
// echo the JS-readable CSRF token on unsafe requests, and refresh once on a 401 — through the
// single-flight refresh the fetch wrapper (http.js) shares (session.js).
const api = axios.create({ baseURL: API_BASE_URL, withCredentials: true });

// Attach the CSRF token (double-submit) on state-changing requests.
api.interceptors.request.use((config) => {
  Object.assign(config.headers, csrfHeaders(config.method));
  return config;
}, (error) => Promise.reject(error));

// On 401, refresh the session once, then retry; if it cannot be refreshed, end the session.
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const original = error.config;
    if (original && !original._retry && shouldRefresh(error.response?.status, original.url)) {
      original._retry = true;
      try {
        await refreshSession();
      } catch (e) {
        endSession();
        return Promise.reject(e);
      }
      return api(original);
    }
    return Promise.reject(error);
  }
);

export const getLatestSensors = async () => {
  try {
    const response = await api.get('/api/cache/sensors');
    return response.data;
  } catch (error) {
    console.error('Error fetching sensors:', error);
    return null;
  }
};

export const getMeasurements = async (sensorId, limit = 100) => {
  try {
    const response = await api.get(`/api/measurements?sensor_id=${sensorId}&limit=${limit}`);
    return response.data;
  } catch (error) {
    console.error('Error fetching measurements:', error);
    return [];
  }
};

export const getCombinedAggregations = async (sensorId, limit = 20) => {
  try {
    const response = await api.get(`/api/aggregations/combined/${sensorId}?limit=${limit}`);
    return response.data;
  } catch (error) {
    console.error('Error fetching combined aggregations:', error);
    return null;
  }
};

export default api;
