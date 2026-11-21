// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { WS_URL } from '../config';
import { refreshSession } from './session';

// Close code the gateway sends when the session's access token has expired (routers/websocket.py).
const SESSION_EXPIRED = 4401;

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
    // The session refresh in flight after a 4401, or null; a reconnect waits on it.
    this.refreshing = null;
    this.refreshedSinceMessage = false;
  }

  /** A socket open or opening, or a reconnect waiting on its backoff or on a refresh. */
  _active() {
    return this.ws !== null || this.reconnectTimer !== null || this.refreshing !== null;
  }

  _listened() {
    return this.messageListeners.size > 0 || this.errorListeners.size > 0;
  }

  _report(error) {
    this.errorListeners.forEach((fn) => fn(error));
  }

  _open() {
    if (this.ws) return;
    let socket;
    try {
      // Same-origin wss:// — the httpOnly access cookie is sent on the handshake and
      // authenticates the connection; no token in the URL (ADR-0004 dec 3).
      socket = new WebSocket(`${WS_URL}/ws/sensors`);
    } catch (error) {
      console.error('Error creating WebSocket:', error);
      return;
    }
    this.ws = socket;
    // Every handler acts only for the current socket: one that was replaced or torn down
    // never starts a second reconnect chain.
    const current = () => this.ws === socket;

    socket.onmessage = (event) => {
      if (!current()) return;
      this.refreshedSinceMessage = false;
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

    socket.onerror = (error) => {
      if (current()) this._report(error);
    };

    socket.onclose = (event) => {
      if (!current()) return;
      this.ws = null;
      // 1008 = policy violation (auth) — do not reconnect; surface it once.
      if (event.code === 1008) {
        this._report(new Error('Authentication failed'));
        return;
      }
      // 4401 = the access cookie expired (the socket lives no longer than it): refresh the
      // session once and reconnect. A second 4401 before any message means the refresh did
      // not restore a session, so stop there.
      if (event.code === SESSION_EXPIRED) {
        if (this.refreshedSinceMessage) {
          this._report(new Error('Session expired'));
          return;
        }
        this.refreshedSinceMessage = true;
        // The one refresh the whole app shares: a rotating refresh token is spent by its first use.
        const refresh = refreshSession().then(
          () => {
            if (this.refreshing !== refresh) return;
            this.refreshing = null;
            if (this._listened()) this._open();
          },
          () => {
            if (this.refreshing !== refresh) return;
            this.refreshing = null;
            this._report(new Error('Session expired'));
          },
        );
        this.refreshing = refresh;
        return;
      }
      // Reconnect for transient closes as long as anyone is still listening.
      if (this._listened()) {
        this.reconnectTimer = setTimeout(() => {
          this.reconnectTimer = null;
          if (this._listened()) this._open();
        }, this.reconnectInterval);
      }
    };
  }

  /**
   * Subscribe to sensor updates. Returns an unsubscribe function; the socket opens on the
   * first subscriber and stays open while any remain. Prefer this in components.
   */
  subscribe(onMessage, onError) {
    if (onMessage) this.messageListeners.add(onMessage);
    if (onError) this.errorListeners.add(onError);
    if (!this._active()) this._open();
    return () => {
      if (onMessage) this.messageListeners.delete(onMessage);
      if (onError) this.errorListeners.delete(onError);
      // The last listener gone: nothing keeps the socket, or a reconnect waiting for one.
      if (!this._listened()) this.disconnect();
    };
  }

  /** Tear the connection down entirely (app teardown). Individual components unsubscribe. */
  disconnect() {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.refreshing = null;
    this.messageListeners.clear();
    this.errorListeners.clear();
    const socket = this.ws;
    this.ws = null;
    if (socket) socket.close();
  }
}

const websocketService = new WebSocketService();
export default websocketService;
