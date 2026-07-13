// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

// Standalone eslint config for the Vite frontend (replaces CRA's `react-app` preset, which came
// from react-scripts). Modern React + hooks rules; the CI job runs `eslint src`.
//
// Flat config (eslint 10 dropped the legacy .eslintrc path entirely). Two things it does NOT
// inherit from the old config and so must state itself: the file glob (`--ext` is gone — flat
// config matches on `files`) and the ignore list (no .eslintignore).
import js from '@eslint/js';
import globals from 'globals';
import react from 'eslint-plugin-react';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';

export default [
  { ignores: ['build/**', 'coverage/**', 'node_modules/**'] },

  js.configs.recommended,
  react.configs.flat.recommended,
  react.configs.flat['jsx-runtime'],
  reactHooks.configs.flat.recommended,

  {
    files: ['**/*.{js,jsx}'],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: globals.browser,
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    settings: { react: { version: 'detect' } },
    plugins: { 'react-refresh': reactRefresh },
    rules: {
      'react/prop-types': 'off',
      // Apostrophes/quotes in JSX text render fine; this cosmetic rule wasn't enforced under CRA.
      'react/no-unescaped-entities': 'off',
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      'no-unused-vars': ['warn', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
      // New in eslint-plugin-react-hooks 7 (the React Compiler rules) — they did not exist under
      // the v4 this config came from, and they flag 18 real findings across 9 components: effects
      // that setState on mount, and effects calling a fetcher declared below them (hoisting, with
      // an empty dep array that lies). Fixing those restructures the data-fetching path, which is
      // a behavioural change and not a linter migration's job — so they are tracked in #214.
      // `warn`, deliberately not `off`: the findings stay in every lint run rather than vanishing.
      'react-hooks/set-state-in-effect': 'warn',
      'react-hooks/immutability': 'warn',
      // Non-breaking spaces are used intentionally inside display template literals (keeping
      // "3 days" on one line); allow them there, still flag stray ones in code.
      'no-irregular-whitespace': ['error', { skipTemplates: true }],
    },
  },

  {
    // Vitest runs with `globals: true` (vite.config.js), so describe/it/expect are ambient in the
    // suites — the browser globals above do not cover them.
    files: ['**/*.test.{js,jsx}', 'src/test/**/*.{js,jsx}'],
    languageOptions: { globals: { ...globals.browser, ...globals.vitest } },
  },
];
