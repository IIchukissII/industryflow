// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import React, { useEffect, useRef, useState } from 'react';
import AppShell from '../components/AppShell';
import './Notebooks.css';

// The notebook hub is mounted same-origin under /jupyter/ (the frontend reverse-proxies it to the
// SSO proxy → hub; ADR-0014). Embedding it here keeps the platform session first-party, so a
// logged-in user lands in their role's environment with no second sign-in. The role decides the
// environment server-side (operators → read-only Voila dashboards; data scientists → authoring),
// so this one surface serves both.
const HUB_URL = '/jupyter/';

export default function Notebooks({ user }) {
  const [loaded, setLoaded] = useState(false);
  const [slow, setSlow] = useState(false);
  const frame = useRef(null);

  // Spawning a per-user environment can take a few seconds; surface that honestly rather than
  // leaving a blank panel.
  useEffect(() => {
    if (loaded) return undefined;
    const t = setTimeout(() => setSlow(true), 6000);
    return () => clearTimeout(t);
  }, [loaded]);

  const reload = () => {
    setLoaded(false);
    setSlow(false);
    if (frame.current) frame.current.src = HUB_URL;
  };

  return (
    <AppShell user={user} title="Notebooks">
      <div className="nb-page">
        <div className="page-head">
          <div>
            <div className="eyebrow">Embedded workspace</div>
            <h1>Notebooks</h1>
            <div className="sub">
              Explore and model your tenant&apos;s data in a sandboxed environment — nothing to
              install, no credentials to manage.
            </div>
          </div>
          <span className={`badge ${loaded ? 'badge-live' : 'badge-warn'}`}>
            <span className={`sdot ${loaded ? 'ok' : 'pending'}`} />
            {loaded ? 'Session live' : 'Connecting'}
          </span>
        </div>

        <section className="panel nb-frame">
          <div className="panel-head nb-frame-head">
            {/* Signature: an instrument-style readout of the isolation posture this product is
                built on — the embed reads as native to a console that foregrounds tenant safety. */}
            <div className="nb-session mono">
              <span className={`sdot ${loaded ? 'ok' : 'pending'}`} />
              <span className="nb-session-label">Session</span>
              <span className="nb-session-sep" aria-hidden="true">/</span>
              <span className="nb-session-meta">single-tenant · read-only data · sandboxed kernel</span>
            </div>
            <div className="nb-actions">
              <button className="nb-action" onClick={reload} title="Restart the embedded session">
                Reload
              </button>
              <a className="nb-action" href={HUB_URL} target="_blank" rel="noreferrer">
                Open in new tab <span aria-hidden="true">↗</span>
              </a>
            </div>
          </div>

          <div className="nb-frame-body">
            {!loaded && (
              <div className="nb-boot" role="status">
                <span className="nb-boot-pulse" aria-hidden="true" />
                <div className="nb-boot-lines mono">
                  <div className="nb-boot-line">establishing secure session</div>
                  <div className="nb-boot-line faint">
                    minting single-tenant capability · spawning your environment
                  </div>
                  {slow && (
                    <div className="nb-boot-line nb-boot-slow">
                      first launch can take a moment while your environment starts —{' '}
                      <a href={HUB_URL} target="_blank" rel="noreferrer">open it in a new tab</a> if
                      it stalls.
                    </div>
                  )}
                </div>
              </div>
            )}
            <iframe
              ref={frame}
              title="IndustryFlow notebooks"
              src={HUB_URL}
              className="nb-iframe"
              onLoad={() => setLoaded(true)}
            />
          </div>
        </section>
      </div>
    </AppShell>
  );
}
