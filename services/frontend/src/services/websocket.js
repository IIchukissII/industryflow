// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { WS_URL } from '../config';

class WebSocketService {
  constructor() {
    this.ws = null;
    this.listeners = [];
    this.reconnectInterval = 3000;
    this.reconnectTimer = null;
  }

  connect(onMessage, onError) {
    try {
      // Same-origin wss:// — the httpOnly access cookie is sent on the handshake and
      // authenticates the connection; no token in the URL (ADR-0004 dec 3).
      this.ws = new WebSocket(`${WS_URL}/ws/sensors`);
      
      this.ws.onopen = () => {
        console.log('WebSocket connected (authenticated)');
        if (this.reconnectTimer) {
          clearTimeout(this.reconnectTimer);
          this.reconnectTimer = null;
        }
      };

      this.ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (onMessage) {
            onMessage(data);
          }
        } catch (error) {
          console.error('Error parsing WebSocket message:', error);
        }
      };

      this.ws.onerror = (error) => {
        console.error('WebSocket error:', error);
        if (onError) {
          onError(error);
        }
      };

      this.ws.onclose = (event) => {
        console.log('WebSocket disconnected:', event.code, event.reason);
        
        // Don't reconnect if closed due to authentication issues
        if (event.code === 1008) {
          console.error('WebSocket closed: Authentication failed');
          if (onError) {
            onError(new Error('Authentication failed'));
          }
          return;
        }
        
        // Reconnect for other closure reasons
        console.log('Reconnecting...');
        this.reconnectTimer = setTimeout(() => {
          this.connect(onMessage, onError);
        }, this.reconnectInterval);
      };
    } catch (error) {
      console.error('Error creating WebSocket:', error);
    }
  }

  disconnect() {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
    }
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }
}

export default new WebSocketService();
