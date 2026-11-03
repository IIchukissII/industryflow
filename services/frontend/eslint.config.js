// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

// Standalone eslint config for the Vite frontend (replaces CRA's `react-app` preset, which came
// from react-scripts). Modern React + hooks rules; the CI job runs `eslint src`.
//
// Flat config (eslint 10 dropped the legacy .eslintrc path entirely). Two things it does NOT
// inherit from the old config and so must state itself: the file glob (`--ext` is gone — flat
// config matches on `files`) and the ignore list (no .eslintignore).
//
// React rules come from @eslint-react; eslint-plugin-react does not run on eslint 10. eslint's own
// scope analysis tracks JSX, so a component used only in JSX counts as used (no-unused-vars) and an
// undefined one is reported (no-undef). The hooks rules stay with eslint-plugin-react-hooks, so the
// @eslint-react rules that repeat them are turned off. Duplicate JSX props, which no maintained
// plugin covers, are the local rule below.
import js from '@eslint/js';
import globals from 'globals';
import eslintReact from '@eslint-react/eslint-plugin';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';

// Two props of one name on an element: the later silently wins.
const noDuplicateProps = {
  meta: { type: 'problem', messages: { dup: 'Prop "{{name}}" is set twice on this element.' }, schema: [] },
  create(context) {
    return {
      JSXOpeningElement(node) {
        const seen = new Set();
        for (const attr of node.attributes) {
          if (attr.type !== 'JSXAttribute') continue;
          const name = attr.name.type === 'JSXNamespacedName'
            ? `${attr.name.namespace.name}:${attr.name.name.name}` : attr.name.name;
          if (seen.has(name)) context.report({ node: attr, messageId: 'dup', data: { name } });
          seen.add(name);
        }
      },
    };
  },
};

// Every @eslint-react rule that eslint-plugin-react-hooks also defines, off: one owner per check, and
// the hooks plugin's, enforced below as errors, is the one this project chose. Derived from the hooks
// plugin itself, so a rule it adds later is not checked twice.
const hooksOwned = Object.fromEntries(
  Object.keys(reactHooks.rules).map((name) => [`@eslint-react/${name}`, 'off']),
);

export default [
  { ignores: ['build/**', 'coverage/**', 'node_modules/**'] },

  js.configs.recommended,
  eslintReact.configs.recommended,
  { rules: hooksOwned },
  reactHooks.configs.flat.recommended,

  {
    files: ['**/*.{js,jsx}'],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: globals.browser,
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    plugins: { 'react-refresh': reactRefresh, local: { rules: { 'jsx-no-duplicate-props': noDuplicateProps } } },
    rules: {
      'local/jsx-no-duplicate-props': 'error',
      // Beyond @eslint-react's recommended set: what eslint-plugin-react's recommended set enforced.
      '@eslint-react/no-missing-component-display-name': 'error',
      '@eslint-react/dom-no-unknown-property': 'error',
      '@eslint-react/dom-no-unsafe-target-blank': 'error',
      // Naming style, not a defect, and not a convention this codebase follows.
      '@eslint-react/naming-convention-ref-name': 'off',
      '@eslint-react/naming-convention-id-name': 'off',
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
