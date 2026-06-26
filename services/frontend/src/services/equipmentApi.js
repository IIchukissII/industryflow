// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import api from './api';

// Equipment CRUD operations
export const getEquipment = async () => {
  const response = await api.get('/api/equipment');
  return response.data;
};

export const getEquipmentById = async (equipmentId) => {
  const response = await api.get(`/api/equipment/${equipmentId}`);
  return response.data;
};

export const createEquipment = async (equipmentData) => {
  const response = await api.post('/api/equipment', equipmentData);
  return response.data;
};

export const updateEquipment = async (equipmentId, equipmentData) => {
  const response = await api.put(`/api/equipment/${equipmentId}`, equipmentData);
  return response.data;
};

export const deleteEquipment = async (equipmentId) => {
  await api.delete(`/api/equipment/${equipmentId}`);
};

// Sensor operations for equipment
export const getEquipmentSensors = async (equipmentId) => {
  const response = await api.get(`/api/equipment/${equipmentId}/sensors`);
  return response.data;
};

export const addSensorToEquipment = async (equipmentId, sensorData) => {
  const response = await api.post(`/api/equipment/${equipmentId}/sensors`, sensorData);
  return response.data;
};

export const addSensorsBulk = async (equipmentId, sensorsData) => {
  const response = await api.post(`/api/equipment/${equipmentId}/sensors/bulk`, sensorsData);
  return response.data;
};

export const removeSensorFromEquipment = async (equipmentId, sensorId) => {
  await api.delete(`/api/equipment/${equipmentId}/sensors/${sensorId}`);
};

// Delete all sensors for equipment (for complete replacement)
export const deleteAllSensors = async (equipmentId, sensorIds) => {
  await Promise.all(
    sensorIds.map(sensorId => 
      api.delete(`/api/equipment/${equipmentId}/sensors/${sensorId}`)
    )
  );
};
