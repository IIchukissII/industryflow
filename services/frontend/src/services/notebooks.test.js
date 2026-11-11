// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { describe, test, expect, vi } from 'vitest';
import { enterNotebooks, notebookOrigin } from './notebooks';

const TICKET = { ticket: 't-123', entry: 'https://nb.example.com/enter', origin: 'https://nb.example.com' };

describe('enterNotebooks', () => {
  test('posts the ticket in a form body into the named frame, never in the URL', async () => {
    const sent = [];
    const submit = vi.spyOn(HTMLFormElement.prototype, 'submit').mockImplementation(function record() {
      sent.push({ method: this.method, action: this.action, target: this.target,
        body: Object.fromEntries(new FormData(this)) });
    });
    const post = vi.fn(async () => ({ data: TICKET }));
    await enterNotebooks({ post, target: 'if-notebooks' });
    submit.mockRestore();
    expect(post).toHaveBeenCalledWith('/api/notebook-ticket');
    expect(sent).toEqual([{ method: 'post', action: TICKET.entry, target: 'if-notebooks', body: { ticket: 't-123' } }]);
    expect(sent[0].action).not.toContain(TICKET.ticket);
    expect(document.querySelector('form')).toBeNull();
    expect(notebookOrigin()).toBe(TICKET.origin);
  });

  test('a refused ticket posts nothing', async () => {
    const submit = vi.spyOn(HTMLFormElement.prototype, 'submit');
    const post = async () => { throw Object.assign(new Error('409'), { response: { status: 409 } }); };
    const refresh = async () => { throw new Error('expired'); };
    await expect(enterNotebooks({ post, target: 'if-notebooks', refresh })).rejects.toThrow('expired');
    expect(submit).not.toHaveBeenCalled();
    submit.mockRestore();
  });

  test('a session not yet continuable is refreshed once, then entered', async () => {
    const submit = vi.spyOn(HTMLFormElement.prototype, 'submit').mockImplementation(() => {});
    const calls = [];
    let first = true;
    const post = async (url) => {
      calls.push(url);
      if (first) { first = false; throw Object.assign(new Error('409'), { response: { status: 409 } }); }
      return { data: TICKET };
    };
    await enterNotebooks({ post, target: 'f', refresh: async () => calls.push('refresh') });
    expect(calls).toEqual(['/api/notebook-ticket', 'refresh', '/api/notebook-ticket']);
    expect(submit).toHaveBeenCalledTimes(1);
    submit.mockRestore();
  });
});
