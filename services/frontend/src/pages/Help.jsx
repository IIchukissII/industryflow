// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useEffect, useRef, useState } from 'react';
import './Help.css';

// The guide is content-first: section data here, rendered as a long-form manual with a sticky
// index. Copy is written from the operator's side of the screen — plain verbs, what you control,
// not how the platform is built.
const SECTIONS = [
  {
    id: 'getting-started',
    label: 'Getting started',
    title: 'Getting started',
    lead: 'IndustryFlow is your control room — live equipment telemetry on one side, the tools to analyze and act on it on the other. Everything you see belongs to your organization alone.',
    blocks: [
      { term: 'Signing in', def: 'Use the email and password your administrator set up. Your session stays signed in on this device until you sign out from the account menu at the bottom of the sidebar.' },
      { term: 'Finding your way', def: 'The sidebar groups everything by what you are doing: Monitor for live data and alerts, Analyze for notebooks, Configure for alert rules and models, and System for this guide and your settings.' },
      { term: 'Your organization and role', def: 'You only ever see your own organization’s data. Operators monitor and respond; data scientists also get notebooks for building models. Your role is shown beneath your email.' },
    ],
    tip: 'Update your profile and preferences any time under System → Settings.',
  },
  {
    id: 'monitoring',
    label: 'Monitoring & alerts',
    title: 'Monitoring & alerts',
    lead: 'The platform streams sensor readings as they arrive and watches them for trouble.',
    blocks: [
      { term: 'The Overview', def: 'The home screen shows live readings for every reporting sensor, grouped by equipment, and plots any channel’s recent history. The stream indicator turns green when data is flowing.' },
      { term: 'Equipment and sensors', def: 'Configure → Equipment lists your monitored units and their sensors. Open a unit to see and adjust its channels.' },
      { term: 'Alert rules', def: 'Configure → Alert Rules is where you set the conditions that raise an alert — a simple threshold, such as temperature above a limit, or a model-based check that flags anomalies.' },
      { term: 'Alert history', def: 'Monitor → Alerts is the record of everything that fired, newest first, so you can see what happened and when.' },
    ],
    tip: 'A model-based alert needs a model set to “production” or “active”. See Notebooks & models to publish one.',
  },
  {
    id: 'notebooks',
    label: 'Notebooks & models',
    title: 'Notebooks & models',
    lead: 'Notebooks are your workbench for exploring data and building models, embedded right in the console.',
    blocks: [
      { term: 'Two kinds of notebook', def: 'Analyze → Notebooks opens your workspace. An analytics view runs a finished notebook as an interactive dashboard; an authoring view — for data scientists — is a full JupyterLab for writing code.' },
      { term: 'Reading your data', def: 'Inside a notebook, the built-in industryflow client reads your organization’s data with no credentials to manage. You can also run SQL directly; unqualified queries resolve to your organization automatically, and the connection is read-only.' },
      { term: 'Tracking experiments', def: 'As you train, log runs, parameters, and metrics with the standard MLflow client. It is already pointed at the platform and scoped to your organization, so you only ever see your own experiments.' },
      { term: 'Registering models', def: 'Register a trained model to publish it to your registry. It appears on the Models page under the plain name you gave it — the platform handles keeping every organization’s models separate.' },
      { term: 'The Models page', def: 'Configure → Models is your registry. Click any model to see its description, metrics, and version history. Notebook models let you edit the description; platform detectors can be promoted to production from there.' },
    ],
  },
  {
    id: 'data',
    label: 'Data & API access',
    title: 'Data & API access',
    lead: 'Your data is yours to read, query, and export — always scoped to your organization.',
    blocks: [
      { term: 'The data API', def: 'Equipment, sensors, and readings are available over the platform’s API for your own integrations, authenticated by your signed-in session.' },
      { term: 'SQL from notebooks', def: 'The notebook SQL connection is read-only and limited to your organization’s data, so you can explore freely without risk of changing anything.' },
      { term: 'Access without passwords', def: 'Behind the scenes, notebooks use short-lived, revocable access grants instead of long-lived passwords. A notebook can read your data without ever holding a credential you would have to rotate.' },
      { term: 'Exporting', def: 'Pull query results into a notebook and export them — CSV, Parquet, or any format you would use for a DataFrame.' },
    ],
  },
];

function Help() {
  const [active, setActive] = useState(SECTIONS[0].id);
  const refs = useRef({});

  // Scrollspy: highlight the index entry for the section nearest the top of the reading area.
  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible[0]) setActive(visible[0].target.id);
      },
      { rootMargin: '-80px 0px -65% 0px', threshold: 0 }
    );
    Object.values(refs.current).forEach((el) => el && observer.observe(el));
    return () => observer.disconnect();
  }, []);

  return (
    <div className="guide">
      <div className="page-head">
        <div>
          <div className="eyebrow">Help</div>
          <h1>User guide</h1>
          <div className="sub">How to operate IndustryFlow — from live monitoring to notebooks and models.</div>
        </div>
      </div>

      <div className="guide-layout">
        <nav className="guide-index" aria-label="Guide sections">
          <div className="guide-index-label eyebrow">Contents</div>
          {SECTIONS.map((s) => (
            <a
              key={s.id}
              href={`#${s.id}`}
              className={`guide-index-item${active === s.id ? ' active' : ''}`}
              aria-current={active === s.id ? 'true' : undefined}
            >
              {s.label}
            </a>
          ))}
        </nav>

        <div className="guide-main">
          {SECTIONS.map((s) => (
            <section
              key={s.id}
              id={s.id}
              className="guide-section"
              ref={(el) => { refs.current[s.id] = el; }}
            >
              <div className="eyebrow guide-section-label">{s.label}</div>
              <h2 className="guide-section-title">{s.title}</h2>
              <p className="guide-lead">{s.lead}</p>

              <dl className="guide-blocks">
                {s.blocks.map((b) => (
                  <div className="guide-block" key={b.term}>
                    <dt>{b.term}</dt>
                    <dd>{b.def}</dd>
                  </div>
                ))}
              </dl>

              {s.tip && (
                <div className="guide-tip">
                  <span className="guide-tip-mark eyebrow">Tip</span>
                  <p>{s.tip}</p>
                </div>
              )}
            </section>
          ))}

          <div className="guide-foot">
            Still stuck? Reach your administrator, or check the platform docs for deeper technical detail.
          </div>
        </div>
      </div>
    </div>
  );
}

export default Help;
