// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

class WebSocketService {
  constructor() {
    this.ws = null;
    this.listeners = [];
    this.reconnectInterval = 3000;
    this.reconnectTimer = null;
  }

  connect(onMessage, onError) {
    try {
      // Get JWT token from localStorage
      const token = localStorage.getItem('access_token');
      
      if (!token) {
        console.error('No authentication token found');
        if (onError) {
          onError(new Error('Not authenticated'));
        }
        return;
      }

      // Include token as query parameter
      this.ws = new WebSocket(`ws://localhost:8000/ws/sensors?token=${token}`);
      
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
