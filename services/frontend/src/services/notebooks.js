// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

// Entering the notebook origin (ADR-0048 dec 3). Notebooks run on an origin of their own, which the
// platform session does not reach: the page asks the platform for a single-use ticket and posts it,
// in a form body (never a URL), into the frame or tab that is to show the hub. The notebook origin
// spends it for a notebook session of its own.

const ORIGIN_KEY = 'if.notebookOrigin';
let knownOrigin = null;

function remember(origin) {
  knownOrigin = origin;
  try {
    globalThis.localStorage?.setItem(ORIGIN_KEY, origin);
  } catch {
    /* storage refused: this page still knows it */
  }
}

// The notebook origin this browser last entered, for sign-out (ADR-0014 dec 7); null if none.
export function notebookOrigin() {
  if (knownOrigin) return knownOrigin;
  try {
    return globalThis.localStorage?.getItem(ORIGIN_KEY) || null;
  } catch {
    return null;
  }
}

// Mint a ticket and post it into `target` (a frame or window name). Resolves once the form is sent;
// rejects with the platform's refusal if no ticket was minted.
export async function enterNotebooks({ post, target, doc = globalThis.document }) {
  const { data } = await post('/api/notebook-ticket');
  remember(data.origin);
  const form = doc.createElement('form');
  form.method = 'POST';
  form.action = data.entry;
  form.target = target;
  form.style.display = 'none';
  const field = doc.createElement('input');
  field.type = 'hidden';
  field.name = 'ticket';
  field.value = data.ticket;
  form.appendChild(field);
  doc.body.appendChild(form);
  try {
    form.submit();
  } finally {
    form.remove();
  }
}
