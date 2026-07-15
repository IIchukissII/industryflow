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
      // The React Compiler rules from eslint-plugin-react-hooks 7. Enforced as errors (#214):
      // fetch-on-mount effects invoke `useCallback`'d fetchers from an inner async function (so no
      // setState runs synchronously in an effect body), prop-derived state is either a lazy
      // `useState` initialiser or reset via a `key` remount, and components that always load on
      // mount start in their loading state. A regression fails the lint run rather than warning.
      'react-hooks/set-state-in-effect': 'error',
      'react-hooks/immutability': 'error',
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
