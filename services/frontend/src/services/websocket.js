// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { WS_URL } from '../config';

/**
 * Single shared sensor WebSocket with a pub/sub fan-out.
 *
 * There is exactly one `/ws/sensors` connection for the whole app; any number of
 * components subscribe to it. This lets the live dashboard AND each open chart react to
 * the same `sensor_update` stream without opening a socket per component or clobbering
 * each other's handler. Subscribers persist across reconnects.
 */
class WebSocketService {
  constructor() {
    this.ws = null;
    this.messageListeners = new Set();
    this.errorListeners = new Set();
    this.reconnectInterval = 3000;
    this.reconnectTimer = null;
  }

  _open() {
    try {
      // Same-origin wss:// — the httpOnly access cookie is sent on the handshake and
      // authenticates the connection; no token in the URL (ADR-0004 dec 3).
      this.ws = new WebSocket(`${WS_URL}/ws/sensors`);

      this.ws.onopen = () => {
        if (this.reconnectTimer) {
          clearTimeout(this.reconnectTimer);
          this.reconnectTimer = null;
        }
      };

      this.ws.onmessage = (event) => {
        let data;
        try {
          data = JSON.parse(event.data);
        } catch (error) {
          console.error('Error parsing WebSocket message:', error);
          return;
        }
        this.messageListeners.forEach((fn) => {
          try {
            fn(data);
          } catch (error) {
            console.error('WebSocket listener failed:', error);
          }
        });
      };

      this.ws.onerror = (error) => {
        this.errorListeners.forEach((fn) => fn(error));
      };

      this.ws.onclose = (event) => {
        this.ws = null;
        // 1008 = policy violation (auth) — do not reconnect; surface it once.
        if (event.code === 1008) {
          this.errorListeners.forEach((fn) => fn(new Error('Authentication failed')));
          return;
        }
        // Reconnect for transient closes as long as anyone is still listening.
        if (this.messageListeners.size || this.errorListeners.size) {
          this.reconnectTimer = setTimeout(() => this._open(), this.reconnectInterval);
        }
      };
    } catch (error) {
      console.error('Error creating WebSocket:', error);
    }
  }

  /**
   * Subscribe to sensor updates. Returns an unsubscribe function; the socket opens on the
   * first subscriber and stays open while any remain. Prefer this in components.
   */
  subscribe(onMessage, onError) {
    if (onMessage) this.messageListeners.add(onMessage);
    if (onError) this.errorListeners.add(onError);
    if (!this.ws) this._open();
    return () => {
      if (onMessage) this.messageListeners.delete(onMessage);
      if (onError) this.errorListeners.delete(onError);
    };
  }

  /**
   * Back-compat entry point (used by the app shell): registers listeners and ensures the
   * connection is open. Equivalent to subscribe() without the unsubscribe handle.
   */
  connect(onMessage, onError) {
    this.subscribe(onMessage, onError);
  }

  /** Tear the connection down entirely (app teardown). Individual components unsubscribe. */
  disconnect() {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.messageListeners.clear();
    this.errorListeners.clear();
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }
}

const websocketService = new WebSocketService();
export default websocketService;
