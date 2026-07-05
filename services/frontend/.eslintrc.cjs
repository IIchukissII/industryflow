// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

// Standalone eslint config for the Vite frontend (replaces CRA's `react-app` preset, which
// came from react-scripts). Modern React + hooks rules; the CI job runs `eslint src`.
module.exports = {
  root: true,
  env: { browser: true, es2021: true },
  parserOptions: { ecmaVersion: 'latest', sourceType: 'module', ecmaFeatures: { jsx: true } },
  settings: { react: { version: 'detect' } },
  extends: [
    'eslint:recommended',
    'plugin:react/recommended',
    'plugin:react/jsx-runtime',
    'plugin:react-hooks/recommended',
  ],
  plugins: ['react-refresh'],
  rules: {
    'react/prop-types': 'off',
    // Apostrophes/quotes in JSX text render fine; this cosmetic rule wasn't enforced under CRA.
    'react/no-unescaped-entities': 'off',
    'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
    'no-unused-vars': ['warn', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
    // Non-breaking spaces are used intentionally inside display template literals (keeping
    // "3 days" on one line); allow them there, still flag stray ones in code.
    'no-irregular-whitespace': ['error', { skipTemplates: true }],
  },
};
