// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

// Central API base URLs. Override per environment with build-time REACT_APP_* vars
// (e.g. the ingress hosts in a Kubernetes deployment — see ADR-0009). The localhost
// fallbacks are for local development only.
export const API_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000';
export const ALERT_API_URL = process.env.REACT_APP_ALERT_URL || 'http://localhost:8001';
export const ML_API_URL = process.env.REACT_APP_ML_URL || 'http://localhost:8002';
export const WS_URL = process.env.REACT_APP_WS_URL || 'ws://localhost:8000';
