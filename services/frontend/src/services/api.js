// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import axios from 'axios';
import { API_URL as API_BASE_URL } from '../config';

// Authentication is cookie-based (ADR-0004 dec 3): the access token lives in an httpOnly
// cookie the browser sends automatically, so we never read or store it in JS. We only
// echo the JS-readable CSRF token on unsafe requests, and refresh once on a 401.
const api = axios.create({ baseURL: API_BASE_URL, withCredentials: true });

export function getCookie(name) {
  const m = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
  return m ? m.pop() : '';
}

// Attach the CSRF token (double-submit) on state-changing requests.
api.interceptors.request.use((config) => {
  const method = (config.method || 'get').toUpperCase();
  if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) {
    const csrf = getCookie('if_csrf');
    if (csrf) config.headers['X-CSRF-Token'] = csrf;
  }
  return config;
}, (error) => Promise.reject(error));

// On 401, try to refresh the session once, then retry; otherwise send the user to login.
let refreshing = null;
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const original = error.config;
    const status = error.response?.status;
    const url = original?.url || '';
    if (status === 401 && original && !original._retry && !url.includes('/auth/')) {
      original._retry = true;
      try {
        refreshing = refreshing || api.post('/auth/refresh');
        await refreshing;
        refreshing = null;
        return api(original);
      } catch (e) {
        refreshing = null;
        localStorage.removeItem('user');
        if (window.location.pathname !== '/login') window.location.href = '/login';
        return Promise.reject(e);
      }
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
