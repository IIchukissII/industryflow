// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { modelsReading } from './reading-models';

const m = (name, origin, extra = {}) => ({ name, origin, live: false, retrain: false, incompatible: false, ...extra });

describe('modelsReading', () => {
  it('names the two ways to add the first model', () => {
    expect(modelsReading({ models: [] })).toBe('No models serve this tenant yet. Upload one, or register one from a notebook.');
  });

  it('counts by origin and says how many are in production', () => {
    const models = [
      m('a', 'notebook'), m('b', 'notebook', { live: true }), m('c', 'platform', { live: true }), m('d', 'uploaded'),
    ];
    expect(modelsReading({ models })).toBe(
      'Four models serve this tenant: two from notebooks, one built in and one uploaded. Two are in production.',
    );
  });

  it('names the origin once when every model shares it', () => {
    const models = [m('a', 'platform', { live: true }), m('b', 'platform', { live: true })];
    expect(modelsReading({ models })).toBe('Two built-in models serve this tenant. All are in production.');
  });

  it('handles a single model that is not live', () => {
    expect(modelsReading({ models: [m('a', 'uploaded')] })).toBe('One uploaded model serves this tenant. It is not in production.');
  });

  it('names the models that need an operator', () => {
    const models = [
      m('detector', 'platform', { live: true, retrain: true }),
      m('ext', 'uploaded', { incompatible: true }),
    ];
    expect(modelsReading({ models })).toBe(
      'Two models serve this tenant: one built in and one uploaded. One is in production. '
      + 'This environment cannot serve ext. Retraining is recommended for detector.',
    );
  });
});
