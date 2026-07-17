// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useEffect, useState } from 'react';
import './AppShell.css';
import api from '../services/api';
import Icon from './Icon';

// Navigation mirrors the mandala's anatomy: the Core (where streams converge and surface),
// the Ground (the sources and their interpretation), the Watch (what guards the Core).
const NAV = [
  {
    group: 'The Core',
    items: [
      { href: '/', label: 'Core', icon: 'grid', exact: true },
      { href: '/alerts', label: 'Alerts', icon: 'bell' },
    ],
  },
  {
    group: 'The Ground',
    items: [
      { href: '/equipment', label: 'Equipment', icon: 'box' },
      { href: '/notebooks', label: 'Notebooks', icon: 'activity' },
    ],
  },
  {
    group: 'The Watch',
    items: [
      { href: '/alert-rules', label: 'Alert Rules', icon: 'sliders' },
      { href: '/ml-models', label: 'Models', icon: 'cpu' },
    ],
  },
];

function NavItem({ href, label, icon, active }) {
  return (
    <a href={href} className={`nav-item${active ? ' active' : ''}`}>
      <Icon name={icon} size={17} />
      <span>{label}</span>
    </a>
  );
}

export default function AppShell({ user, title, wsConnected, lastUpdate, children }) {
  const [apiUp, setApiUp] = useState(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    const ping = () =>
      fetch('/health')
        .then((r) => alive && setApiUp(r.ok))
        .catch(() => alive && setApiUp(false));
    ping();
    const t = setInterval(ping, 15000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  const handleLogout = async () => {
    try { await api.post('/auth/logout'); } catch { /* best-effort revoke */ }
    localStorage.removeItem('user');
    window.location.href = '/login';
  };

  const path = typeof window !== 'undefined' ? window.location.pathname : '/';
  const isActive = (item) => (item.exact ? path === item.href : path.startsWith(item.href));

  return (
    <div className={`shell${open ? ' nav-open' : ''}`}>
      <aside className="rail">
        <a href="/" className="brand">
          <img src="/logo-mark.svg" alt="" width="30" height="30" />
          <span className="brand-name">Industry<span className="wm-accent">Flow</span></span>
        </a>

        <nav className="nav">
          {NAV.map((sec) => (
            <div className="nav-group" key={sec.group}>
              <div className="nav-group-label eyebrow">{sec.group}</div>
              {sec.items.map((it) => (
                <NavItem key={it.href} {...it} active={isActive(it)} />
              ))}
            </div>
          ))}
          <div className="nav-group">
            <div className="nav-group-label eyebrow">System</div>
            <NavItem href="/help" label="Help" icon="help" active={isActive({ href: '/help' })} />
            <NavItem href="/settings" label="Settings" icon="gear" active={isActive({ href: '/settings' })} />
            {user && user.is_superuser && (
              <NavItem href="/admin" label="Admin" icon="users" active={isActive({ href: '/admin' })} />
            )}
          </div>
        </nav>

        <div className="rail-foot">
          <div className="acct">
            <span className="acct-badge"><Icon name="user" size={15} /></span>
            <span className="acct-meta">
              <span className="acct-email">{user ? user.email : 'Guest'}</span>
              <span className="acct-role mono">{user ? (user.is_superuser ? 'superuser' : user.role || 'member') : ''}</span>
            </span>
          </div>
          <button className="icon-btn" title="Sign out" onClick={handleLogout}>
            <Icon name="logout" size={16} />
          </button>
        </div>
      </aside>

      <div className="shell-main">
        <header className="topbar">
          <button className="icon-btn rail-toggle" onClick={() => setOpen((v) => !v)} title="Menu">
            <Icon name="menu" size={18} />
          </button>
          <h1 className="topbar-title">{title}</h1>
          <div className="topbar-status">
            {lastUpdate && <span className="ts mono">updated {lastUpdate}</span>}
            {wsConnected !== undefined && (
              <span className="stat">
                <span className={`sdot ${wsConnected ? 'ok' : 'pending'}`} />
                {wsConnected ? 'Live' : 'Connecting'}
              </span>
            )}
            <span className="stat">
              <span className={`sdot ${apiUp === null ? 'pending' : apiUp ? 'ok' : 'bad'}`} />
              {apiUp === false ? 'API offline' : 'API'}
            </span>
          </div>
        </header>

        <main className="content">{children}</main>
      </div>

      <div className="rail-scrim" onClick={() => setOpen(false)} />
    </div>
  );
}
