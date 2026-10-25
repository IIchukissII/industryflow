// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useEffect, useState } from 'react';
import './AppShell.css';
import api from '../services/api';
import authFetch from '../services/http';
import { signOut } from '../services/session';
import Icon from './Icon';

// Navigation mirrors the mandala's anatomy: the Core (where streams converge and surface),
// the Ground (the sources and their interpretation), the Watch (what guards the Core).
const NAV = [
  {
    group: 'The core',
    items: [
      { href: '/', label: 'Core', icon: 'grid', exact: true },
      { href: '/alerts', label: 'Alerts', icon: 'bell', count: 'openAlerts' },
    ],
  },
  {
    group: 'The ground',
    items: [
      { href: '/equipment', label: 'Equipment', icon: 'box' },
      { href: '/notebooks', label: 'Notebooks', icon: 'activity' },
      { href: '/experiments', label: 'Experiments', icon: 'flask' },
    ],
  },
  {
    group: 'The watch',
    items: [
      { href: '/alert-rules', label: 'Alert rules', icon: 'sliders' },
      { href: '/ml-models', label: 'Models', icon: 'cpu' },
    ],
  },
];

const OPEN_ALERTS_POLL_MS = 30000;

function NavItem({ href, label, icon, active, count }) {
  return (
    <a href={href} className={`nav-item${active ? ' active' : ''}`} aria-current={active ? 'page' : undefined}>
      <Icon name={icon} size={17} />
      <span>{label}</span>
      {count > 0 && (
        <span className="nav-count" aria-label={`${count} open`}>{count}</span>
      )}
    </a>
  );
}

// The one status line the shell shows: the live stream on pages that stream, otherwise nothing
// unless the API is unreachable.
function Status({ wsConnected, lastUpdate, apiUp }) {
  if (apiUp === false) {
    return <span className="stat stat-bad"><span className="sdot bad" />API unreachable</span>;
  }
  if (wsConnected === undefined) return null;
  return (
    <span className="stat">
      <span className={`sdot ${wsConnected ? 'ok' : 'pending'}`} />
      <span>
        {wsConnected ? 'Live' : 'Connecting'}
        {lastUpdate && <span className="stat-time">, last reading <span className="mono">{lastUpdate}</span></span>}
      </span>
    </span>
  );
}

export default function AppShell({ user, title, wsConnected, lastUpdate, children }) {
  const [apiUp, setApiUp] = useState(null);
  const [openAlerts, setOpenAlerts] = useState(0);
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

  // Open (unacknowledged) alerts, counted beside Alerts in the nav on every page.
  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const res = await authFetch('/api/alerts?acknowledged=false&limit=500');
        if (!res.ok) return;
        const data = await res.json();
        if (alive) setOpenAlerts(Array.isArray(data) ? data.length : 0);
      } catch { /* best-effort; the count just stays as it was */ }
    };
    load();
    const t = setInterval(load, OPEN_ALERTS_POLL_MS);
    return () => { alive = false; clearInterval(t); };
  }, []);

  const handleLogout = async () => {
    await signOut({ post: (url) => api.post(url) });
    localStorage.removeItem('user');
    window.location.href = '/login';
  };

  const path = typeof window !== 'undefined' ? window.location.pathname : '/';
  const isActive = (item) => (item.exact ? path === item.href : path.startsWith(item.href));
  const counts = { openAlerts };

  return (
    <div className={`shell${open ? ' nav-open' : ''}`}>
      <aside className="rail">
        <a href="/" className="brand">
          <img src="/logo-mark.svg" alt="" width="28" height="28" />
          <span className="brand-name">Industry<span className="wm-accent">Flow</span></span>
        </a>

        <nav className="nav" aria-label="Main">
          {NAV.map((sec) => (
            <div className="nav-group" key={sec.group}>
              <div className="nav-group-label">{sec.group}</div>
              {sec.items.map((it) => (
                <NavItem key={it.href} {...it} active={isActive(it)} count={it.count ? counts[it.count] : 0} />
              ))}
            </div>
          ))}
        </nav>

        <div className="rail-foot">
          <div className="rail-links">
            <NavItem href="/help" label="Help" icon="help" active={isActive({ href: '/help' })} />
            <NavItem href="/settings" label="Settings" icon="gear" active={isActive({ href: '/settings' })} />
            {user && user.is_superuser && (
              <NavItem href="/admin" label="Admin" icon="users" active={isActive({ href: '/admin' })} />
            )}
          </div>
          <div className="acct">
            <span className="acct-meta">
              <span className="acct-email">{user ? user.email : 'Guest'}</span>
              <span className="acct-role">{user ? (user.is_superuser ? 'Superuser' : user.role || 'Member') : ''}</span>
            </span>
            <button type="button" className="icon-btn" aria-label="Sign out" title="Sign out" onClick={handleLogout}>
              <Icon name="logout" size={16} />
            </button>
          </div>
        </div>
      </aside>

      <div className="shell-main">
        <header className="topbar">
          <button
            type="button" className="icon-btn rail-toggle" aria-label="Open menu" aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
          >
            <Icon name="menu" size={18} />
          </button>
          <h1 className="topbar-title">{title}</h1>
          <div className="topbar-status">
            <Status wsConnected={wsConnected} lastUpdate={lastUpdate} apiUp={apiUp} />
          </div>
        </header>

        <main className="content">{children}</main>
      </div>

      <div className="rail-scrim" onClick={() => setOpen(false)} />
    </div>
  );
}
