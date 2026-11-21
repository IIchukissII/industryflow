// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';

// The socket lives no longer than the access cookie: the gateway closes it with 4401 when the
// token expires, and the client refreshes the session once and reconnects. 1008 is final.
const post = vi.fn();
vi.mock('./session', () => ({ refreshSession: (...a) => post('/auth/refresh', ...a) }));

class FakeSocket {
  static instances = [];
  constructor(url) {
    this.url = url;
    FakeSocket.instances.push(this);
  }
  close() {}
}

const flush = () => new Promise((r) => setTimeout(r, 0));

describe('websocket session handling', () => {
  let service;

  beforeEach(async () => {
    FakeSocket.instances = [];
    post.mockReset();
    vi.stubGlobal('WebSocket', FakeSocket);
    vi.resetModules();
    service = (await import('./websocket')).default;
  });

  afterEach(() => {
    service.disconnect();
    vi.unstubAllGlobals();
  });

  test('the URL carries no token', () => {
    service.subscribe(() => {});
    expect(FakeSocket.instances[0].url).toMatch(/\/ws\/sensors$/);
    expect(FakeSocket.instances[0].url).not.toContain('token');
  });

  test('4401 refreshes the session and reconnects', async () => {
    post.mockResolvedValue({});
    service.subscribe(() => {});
    FakeSocket.instances[0].onclose({ code: 4401 });
    await flush();
    expect(post).toHaveBeenCalledWith('/auth/refresh');
    expect(FakeSocket.instances).toHaveLength(2);
  });

  test('a second 4401 with no message in between stops and reports', async () => {
    post.mockResolvedValue({});
    const onError = vi.fn();
    service.subscribe(() => {}, onError);
    FakeSocket.instances[0].onclose({ code: 4401 });
    await flush();
    FakeSocket.instances[1].onclose({ code: 4401 });
    await flush();
    expect(post).toHaveBeenCalledTimes(1);
    expect(FakeSocket.instances).toHaveLength(2);
    expect(onError).toHaveBeenCalled();
  });

  test('a failed refresh reports and does not reconnect', async () => {
    post.mockRejectedValue(new Error('401'));
    const onError = vi.fn();
    service.subscribe(() => {}, onError);
    FakeSocket.instances[0].onclose({ code: 4401 });
    await flush();
    expect(FakeSocket.instances).toHaveLength(1);
    expect(onError).toHaveBeenCalled();
  });

  test('1008 is final', async () => {
    const onError = vi.fn();
    service.subscribe(() => {}, onError);
    FakeSocket.instances[0].onclose({ code: 1008 });
    await flush();
    expect(post).not.toHaveBeenCalled();
    expect(FakeSocket.instances).toHaveLength(1);
    expect(onError).toHaveBeenCalled();
  });

  test('a subscribe during the reconnect backoff opens no second socket', async () => {
    vi.useFakeTimers();
    try {
      service.subscribe(() => {});
      FakeSocket.instances[0].onclose({ code: 1006 });
      service.subscribe(() => {});
      expect(FakeSocket.instances).toHaveLength(1);
      vi.advanceTimersByTime(service.reconnectInterval);
      expect(FakeSocket.instances).toHaveLength(2);
      service.subscribe(() => {});
      expect(FakeSocket.instances).toHaveLength(2);
    } finally {
      vi.useRealTimers();
    }
  });

  test('a subscribe during the 4401 refresh opens no second socket', async () => {
    let settle;
    post.mockReturnValue(new Promise((r) => { settle = r; }));
    service.subscribe(() => {});
    FakeSocket.instances[0].onclose({ code: 4401 });
    service.subscribe(() => {});
    expect(FakeSocket.instances).toHaveLength(1);
    settle({});
    await flush();
    expect(FakeSocket.instances).toHaveLength(2);
  });

  test('a replaced socket closing starts no reconnect', async () => {
    vi.useFakeTimers();
    try {
      service.subscribe(() => {});
      const first = FakeSocket.instances[0];
      first.onclose({ code: 1006 });
      vi.advanceTimersByTime(service.reconnectInterval);
      first.onclose({ code: 1006 });
      vi.advanceTimersByTime(service.reconnectInterval);
      expect(FakeSocket.instances).toHaveLength(2);
    } finally {
      vi.useRealTimers();
    }
  });

  test('disconnect during a refresh leaves no socket behind', async () => {
    let settle;
    post.mockReturnValue(new Promise((r) => { settle = r; }));
    service.subscribe(() => {});
    FakeSocket.instances[0].onclose({ code: 4401 });
    service.disconnect();
    settle({});
    await flush();
    expect(FakeSocket.instances).toHaveLength(1);
  });

  test('the last listener leaving closes the socket and cancels a pending refresh', async () => {
    let settle;
    post.mockReturnValue(new Promise((r) => { settle = r; }));
    const unsubscribe = service.subscribe(() => {});
    FakeSocket.instances[0].onclose({ code: 4401 });
    unsubscribe();
    settle({});
    await flush();
    expect(FakeSocket.instances).toHaveLength(1);
  });
});
