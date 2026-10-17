// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useState, lazy, Suspense } from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import './App.css';
import AppShell from './components/AppShell';
import Core from './pages/Core';

// Route pages are code-split: each becomes its own chunk, loaded on demand, so the initial
// bundle is the shell and Core rather than one big blob.
const AlertRules = lazy(() => import('./pages/AlertRules'));
const AlertHistory = lazy(() => import('./pages/AlertHistory'));
const AdminPanel = lazy(() => import('./pages/AdminPanel'));
const Equipment = lazy(() => import('./pages/Equipment'));
const Settings = lazy(() => import('./pages/Settings'));
const MLModels = lazy(() => import('./pages/MLModels'));
const Notebooks = lazy(() => import('./pages/Notebooks'));
const Help = lazy(() => import('./pages/Help'));
const Login = lazy(() => import('./pages/Login'));

function PageFallback() {
  return <div className="app-fallback"><span className="sdot pending" /> Loading…</div>;
}

function ProtectedRoute({ children, user }) {
  if (!user) {
    return <Navigate to="/login" replace />;
  }
  return children;
}

function App() {
  // The access token is an httpOnly cookie (not visible to JS); restore the session synchronously
  // from the saved (non-sensitive) user profile at first render — there is nothing to wait for, so
  // no loading gate. An invalid/expired cookie surfaces as a 401 on the first API call, which the
  // api client handles (refresh, else redirect to login).
  const [user, setUser] = useState(() => {
    const savedUser = localStorage.getItem('user');
    return savedUser ? JSON.parse(savedUser) : null;
  });

  const handleLogin = (userData) => {
    setUser(userData);
  };

  return (
    <Router>
      <Suspense fallback={<PageFallback />}>
      <Routes>
        <Route path="/login" element={
          user ? <Navigate to="/" replace /> : <Login onLogin={handleLogin} />
        } />
        <Route path="/" element={
          <ProtectedRoute user={user}>
            <Core user={user} />
          </ProtectedRoute>
        } />
        <Route path="/alerts" element={
          <ProtectedRoute user={user}>
            <AppShell user={user} title="Alerts"><AlertHistory /></AppShell>
          </ProtectedRoute>
        } />
        <Route path="/alert-rules" element={
          <ProtectedRoute user={user}>
            <AppShell user={user} title="Alert rules"><AlertRules /></AppShell>
          </ProtectedRoute>
        } />
        <Route path="/equipment" element={
          <ProtectedRoute user={user}>
            <AppShell user={user} title="Equipment"><Equipment /></AppShell>
          </ProtectedRoute>
        } />
        <Route path="/ml-models" element={
          <ProtectedRoute user={user}>
            <AppShell user={user} title="Models"><MLModels /></AppShell>
          </ProtectedRoute>
        } />
        <Route path="/notebooks" element={
          <ProtectedRoute user={user}>
            <Notebooks user={user} />
          </ProtectedRoute>
        } />
        <Route path="/help" element={
          <ProtectedRoute user={user}>
            <AppShell user={user} title="Help"><Help user={user} /></AppShell>
          </ProtectedRoute>
        } />
        <Route path="/settings" element={
          <ProtectedRoute user={user}>
            <AppShell user={user} title="Settings"><Settings /></AppShell>
          </ProtectedRoute>
        } />
        <Route path="/admin/*" element={
          <ProtectedRoute user={user}>
            <AppShell user={user} title="Admin"><AdminPanel /></AppShell>
          </ProtectedRoute>
        } />
      </Routes>
      </Suspense>
    </Router>
  );
}

export default App;
