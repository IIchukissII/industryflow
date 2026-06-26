// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import axios from 'axios';
import { API_URL as API_BASE_URL } from '../config';

// Create axios instance with auth interceptor
const api = axios.create({
  baseURL: API_BASE_URL
});

// Add auth token to all requests
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
}, (error) => {
  return Promise.reject(error);
});

// Add response interceptor for error handling
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      // Token expired or invalid - redirect to login
      localStorage.removeItem('access_token');
      localStorage.removeItem('user');
      window.location.href = '/login';
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
