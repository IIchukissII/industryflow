// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

// Models: how many models serve the tenant, where they came from, how many are in production,
// and which need an operator (a retrain recommendation, or an environment that cannot serve them).

import { countWord, listNames } from './reading';

const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

const ORIGIN_WORDS = { notebook: 'from notebooks', platform: 'built in', uploaded: 'uploaded' };
const ORIGIN_ADJ = { notebook: 'notebook', platform: 'built-in', uploaded: 'uploaded' };

// models: [{ name, origin: 'notebook'|'platform'|'uploaded', live, retrain, incompatible }]
export function modelsReading({ models = [] }) {
  const n = models.length;
  if (!n) return 'No models serve this tenant yet. Upload one, or register one from a notebook.';

  const byOrigin = ['notebook', 'platform', 'uploaded']
    .map((o) => [o, models.filter((m) => m.origin === o).length])
    .filter(([, k]) => k > 0);
  const serve = n === 1 ? 'model serves' : 'models serve';
  const parts = [];
  if (byOrigin.length === 1) {
    parts.push(`${cap(countWord(n))} ${ORIGIN_ADJ[byOrigin[0][0]]} ${serve} this tenant.`);
  } else {
    const split = listNames(byOrigin.map(([o, k]) => `${countWord(k)} ${ORIGIN_WORDS[o]}`));
    parts.push(`${cap(countWord(n))} ${serve} this tenant: ${split}.`);
  }

  const live = models.filter((m) => m.live).length;
  if (!live) parts.push(n === 1 ? 'It is not in production.' : 'None is in production.');
  else if (live === n) parts.push(n === 1 ? 'It is in production.' : 'All are in production.');
  else parts.push(`${cap(countWord(live))} ${live === 1 ? 'is' : 'are'} in production.`);

  const names = (list) => listNames(list.map((m) => m.name));
  const incompatible = models.filter((m) => m.incompatible);
  if (incompatible.length) {
    parts.push(incompatible.length === 1
      ? `This environment cannot serve ${names(incompatible)}.`
      : `This environment cannot serve ${countWord(incompatible.length)} of them: ${names(incompatible)}.`);
  }
  const retrain = models.filter((m) => m.retrain);
  if (retrain.length) {
    parts.push(retrain.length === 1
      ? `Retraining is recommended for ${names(retrain)}.`
      : `Retraining is recommended for ${countWord(retrain.length)}: ${names(retrain)}.`);
  }
  return parts.join(' ');
}
