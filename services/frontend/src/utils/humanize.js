// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

// A stored machine key shown to people: `centrifugal_pump` -> `Centrifugal pump`.
export const humanize = (t) => (t ? (t.charAt(0).toUpperCase() + t.slice(1)).replace(/[-_]+/g, ' ') : '');
