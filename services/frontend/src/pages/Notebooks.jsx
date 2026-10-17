// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useEffect, useRef, useState } from 'react';
import AppShell from '../components/AppShell';
import { notebooksReading } from '../utils/reading-notebooks';
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
      <div className="nb">
        <div className="nb-head">
          <p className="reading">{notebooksReading({ loaded, slow })}</p>
          <div className="nb-actions">
            <button type="button" className="btn" onClick={reload}>Reload</button>
            <a className="btn" href={HUB_URL} target="_blank" rel="noreferrer">Open in a new tab</a>
          </div>
        </div>

        <section className="nb-frame" aria-label="Notebook environment">
          {!loaded && (
            <div className="nb-boot" role="status">
              <div className="nb-boot-inner">
                <span className="nb-boot-pulse" aria-hidden="true" />
                <div className="nb-boot-lines">
                  <p>Starting your environment</p>
                  <p className="nb-boot-sub">
                    Its data access is issued for your tenant only, and its kernel runs in a sandbox.
                  </p>
                  {slow && (
                    <p className="nb-boot-sub">
                      If nothing appears, <a href={HUB_URL} target="_blank" rel="noreferrer">open it in a new tab</a> to
                      see what the hub reports.
                    </p>
                  )}
                </div>
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
        </section>
      </div>
    </AppShell>
  );
}
