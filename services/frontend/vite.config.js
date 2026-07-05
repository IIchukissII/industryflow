// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

/// <reference types="vitest/config" />
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

// Migrated from Create React App. Output stays in build/ (not Vite's default dist/) so the
// Dockerfile COPY and the nginx web root are unchanged. In production the app is served
// same-origin behind nginx; for `npm run dev` the dev server proxies the API prefixes to a
// local gateway so cookies/CSRF stay first-party.
export default defineConfig({
  plugins: [react()],
  // Use the automatic JSX runtime everywhere (incl. the vitest transform) so components that
  // don't import React (React 17+ / the new transform) render in tests too.
  esbuild: { jsx: 'automatic' },
  build: {
    outDir: 'build',
    // CRA emitted source maps off by default in prod; keep the bundle lean.
    sourcemap: false,
  },
  server: {
    port: 3000,
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
      '/auth': { target: 'http://localhost:8000', changeOrigin: true },
      '/users': { target: 'http://localhost:8000', changeOrigin: true },
      '/ws': { target: 'ws://localhost:8000', ws: true },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.js',
    css: false,
  },
});
