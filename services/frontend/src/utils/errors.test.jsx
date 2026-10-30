// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { detailText, errorText } from './errors';

describe('errorText', () => {
  it('reads a refused body as its fields and messages, never as objects', () => {
    const err = { response: { data: { detail: [
      { loc: ['body', 'equipment_id'], msg: 'Input should be a valid UUID' },
      { loc: ['body', 'sensor_count'], msg: 'Input should be greater than 0' },
    ] } } };
    expect(errorText(err)).toBe('equipment_id: Input should be a valid UUID; sensor_count: Input should be greater than 0');
  });

  it('reads a string, a message or a reason, then the transport error', () => {
    expect(errorText({ response: { data: { detail: 'Equipment not found' } } })).toBe('Equipment not found');
    expect(detailText({ message: 'refused' })).toBe('refused');
    expect(detailText({ reason: 'not configured' })).toBe('not configured');
    expect(errorText({ message: 'Network Error' })).toBe('Network Error');
    expect(errorText({ response: { data: { detail: {} } }, message: 'x' })).toBe('x');
  });
});
