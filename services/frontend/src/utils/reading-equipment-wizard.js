// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

// The equipment wizard's sentence for each step: what this step asks for, in words.

import { countWord } from './reading';

const machine = (name) => (name && name.trim()) || 'this machine';
const sensors = (n) => `${countWord(n)} sensor${n === 1 ? '' : 's'}`;

export function wizardReading({ step, editing = false, name = '', position = 0, total = 0, configured = 0 }) {
  if (step === 1) {
    return {
      lede: editing ? `What ${machine(name)} is, and where it stands.` : 'Name the machine, and say how many sensors report from it.',
    };
  }
  if (step === 2) {
    const done = configured === 0 ? 'None is set up yet.' : `${cap(countWord(configured))} of ${countWord(total)} set up.`;
    return { lede: `Sensor ${position + 1} of ${total} on ${machine(name)}.`, sub: done };
  }
  const missing = total - configured;
  if (missing <= 0) {
    return { lede: `All ${sensors(total)} of ${machine(name)} are set up.`, pending: false };
  }
  return {
    lede: `${cap(countWord(configured))} of ${countWord(total)} sensors on ${machine(name)} ${configured === 1 ? 'is' : 'are'} set up.`,
    sub: missing === 1 ? 'Set up the last one to save.' : `Set up the remaining ${countWord(missing)} to save.`,
    pending: true,
  };
}

const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
