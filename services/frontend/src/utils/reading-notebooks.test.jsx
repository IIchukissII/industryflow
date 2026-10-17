// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { notebooksReading } from './reading-notebooks';

describe('notebooksReading', () => {
  it('says the environment is starting until the hub answers', () => {
    expect(notebooksReading({})).toMatch(/^Your notebook environment is starting\./);
  });

  it('says a slow start is still a start', () => {
    expect(notebooksReading({ slow: true })).toMatch(/^Your environment is still starting\./);
  });

  it('says the environment is open once loaded, whatever the timer said', () => {
    expect(notebooksReading({ loaded: true, slow: true })).toMatch(/^Your notebook environment is open\./);
  });
});
