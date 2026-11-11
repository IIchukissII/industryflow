// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useCallback, useEffect, useRef, useState } from 'react';
import AppShell from '../components/AppShell';
import api from '../services/api';
import { enterNotebooks } from '../services/notebooks';
import { errorText } from '../utils/errors';
import { notebooksReading } from '../utils/reading-notebooks';
import './Notebooks.css';

// The notebook hub runs on an origin of its own (ADR-0048), so script a kernel produces never runs
// with the platform session. This page frames that origin and enters it with a single-use ticket
// posted into the frame; the user lands in their role's environment with no second sign-in. The role
// decides the environment server-side (operators: read-only Voila dashboards; data scientists:
// authoring), so this one surface serves both.
const FRAME = 'if-notebooks';

export default function Notebooks({ user }) {
  const [loaded, setLoaded] = useState(false);
  const [slow, setSlow] = useState(false);
  const [refused, setRefused] = useState(null);
  // Set once a ticket is posted into the frame: the frame's earlier load is its empty document.
  const entering = useRef(false);

  const post = useCallback((url) => api.post(url), []);

  // Post a ticket into the frame; a refusal is shown once the platform has answered.
  const enter = useCallback(() => {
    entering.current = true;
    return enterNotebooks({ post, target: FRAME }).catch((err) => {
      entering.current = false;
      setRefused(errorText(err));
    });
  }, [post]);

  useEffect(() => {
    enter();
  }, [enter]);

  const reload = () => {
    entering.current = false;
    setLoaded(false);
    setSlow(false);
    setRefused(null);
    enter();
  };

  // Spawning a per-user environment can take a few seconds; surface that honestly rather than
  // leaving a blank panel.
  useEffect(() => {
    if (loaded || refused) return undefined;
    const t = setTimeout(() => setSlow(true), 6000);
    return () => clearTimeout(t);
  }, [loaded, refused]);

  // A tab of its own, opened within the click so it is not blocked; its opener is cut before the
  // notebook origin is loaded into it.
  const openInTab = async () => {
    const tab = window.open('', `${FRAME}-tab`);
    if (!tab) return;
    tab.opener = null;
    try {
      await enterNotebooks({ post, target: `${FRAME}-tab` });
    } catch (err) {
      tab.close();
      setRefused(errorText(err));
    }
  };

  return (
    <AppShell user={user} title="Notebooks">
      <div className="nb">
        <div className="nb-head">
          <p className="reading">{notebooksReading({ loaded, slow, refused })}</p>
          <div className="nb-actions">
            <button type="button" className="btn" onClick={reload}>Reload</button>
            <button type="button" className="btn" onClick={openInTab}>Open in a new tab</button>
          </div>
        </div>

        <section className="nb-frame" aria-label="Notebook environment">
          {!loaded && !refused && (
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
                      If nothing appears, open it in a new tab to see what the hub reports.
                    </p>
                  )}
                </div>
              </div>
            </div>
          )}
          <iframe
            name={FRAME}
            title="IndustryFlow notebooks"
            className="nb-iframe"
            onLoad={() => { if (entering.current) setLoaded(true); }}
          />
        </section>
      </div>
    </AppShell>
  );
}
