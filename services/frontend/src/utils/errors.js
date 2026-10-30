// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

// A request's failure as one line to show. An API answers with `detail` as a string, an object
// carrying `message` or `reason`, or (a refused body) a list of `{loc, msg}`; rendering anything but
// text would throw in React.
export function detailText(detail) {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    const parts = detail.map((d) => {
      const field = Array.isArray(d?.loc) ? d.loc.filter((p) => p !== 'body').join('.') : '';
      const msg = typeof d?.msg === 'string' ? d.msg : '';
      return field && msg ? `${field}: ${msg}` : msg || field;
    }).filter(Boolean);
    return parts.length ? parts.join('; ') : null;
  }
  if (detail && typeof detail.message === 'string') return detail.message;
  if (detail && typeof detail.reason === 'string') return detail.reason;
  return null;
}

export function errorText(err) {
  return detailText(err?.response?.data?.detail) || err?.message || 'The request failed.';
}
