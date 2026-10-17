// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useEffect, useRef, useState } from 'react';
import './Help.css';
import { helpReading } from '../utils/reading-help';

// The guide is content-first: section data here, rendered as a long-form manual with a sticky
// index. Copy is written from the operator's side of the screen: plain verbs, what you control,
// not how the platform is built. Names match the sidebar.
const SECTIONS = [
  {
    id: 'getting-started',
    title: 'Getting started',
    lead: 'IndustryFlow shows your equipment’s live readings and gives you the tools to act on them. Everything you see belongs to your organization alone.',
    blocks: [
      { term: 'Signing in', def: 'Use the email and password your administrator set up. You stay signed in on this device until you sign out with the button beside your email at the bottom of the sidebar.' },
      { term: 'Finding your way', def: 'The sidebar has three groups. The core holds the Core and Alerts: what is happening now. The ground holds Equipment and Notebooks: the machines and the work on their data. The watch holds Alert rules and Models: what decides when to raise an alert.' },
      { term: 'Your role', def: 'Observers see everything but change nothing. Engineers also change equipment, alert rules and models, and handle alerts. Admins also manage the users of their organization. Your role is shown beneath your email.' },
    ],
    tip: 'Change your password under Settings.',
  },
  {
    id: 'monitoring',
    title: 'Monitoring and alerts',
    lead: 'Readings stream in as devices send them, and alert rules watch them for trouble.',
    blocks: [
      { term: 'The Core', def: 'The home screen, where every stream converges. Each reporting sensor is a node on a ring, grouped by machine and tinted by its open alerts; each new reading pulses toward the centre. Select a node to read its value and plot its recent history.' },
      { term: 'Equipment', def: 'Lists your machines and their sensors. Open a machine to see its channels; engineers and admins add, edit and delete machines here.' },
      { term: 'Alert rules', def: 'Set the conditions that raise an alert: a threshold, such as temperature above a limit, or a model that flags anomalies.' },
      { term: 'Alerts', def: 'Everything that fired, open alerts first. Acknowledge an alert once it is handled, and mark whether it was real so the rules can be judged by how often they are right.' },
    ],
    tip: 'A model-based rule needs a model in production. Notebooks and models explains how to publish one.',
  },
  {
    id: 'notebooks',
    title: 'Notebooks and models',
    lead: 'Notebooks are your workbench for exploring data and building models, inside the console.',
    blocks: [
      { term: 'Two kinds of notebook', def: 'Notebooks opens your workspace. Depending on your role, it opens either as finished notebooks run as interactive dashboards, or as a full JupyterLab for writing code. Your deployment decides which roles get which.' },
      { term: 'Reading your data', def: 'Inside a notebook, the built-in industryflow client reads your organization’s data with no credentials to manage. You can also run SQL; the connection is read-only and limited to your organization.' },
      { term: 'Tracking experiments', def: 'Log runs, parameters and metrics with the standard MLflow client. It is already set up for your organization, so you only see your own experiments.' },
      { term: 'Registering models', def: 'Register a trained model to publish it. It appears on the Models page under the name you gave it.' },
      { term: 'The Models page', def: 'Your registry. Select a model to see its description, metrics and version history. Engineers and admins can also upload a model trained elsewhere and promote a model to production.' },
    ],
  },
  {
    id: 'data',
    title: 'Data and API access',
    lead: 'Your data is yours to read, query and export, always limited to your organization.',
    blocks: [
      { term: 'The data API', def: 'Equipment, sensors and readings are available over the platform’s API for your own integrations, using your signed-in session.' },
      { term: 'SQL from notebooks', def: 'The notebook SQL connection is read-only and limited to your organization’s data, so you can explore without changing anything.' },
      { term: 'Access without passwords', def: 'Notebooks use short-lived, revocable access grants instead of passwords, so a notebook never holds a credential you would have to rotate.' },
      { term: 'Exporting', def: 'Load query results into a notebook and save them as CSV, Parquet or any format a DataFrame writes.' },
    ],
  },
];

function Help({ user }) {
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
      <p className="reading guide-reading">{helpReading({ user })}</p>

      <div className="guide-layout">
        <nav className="guide-index" aria-label="Guide sections">
          <p className="guide-index-label">Contents</p>
          {SECTIONS.map((s) => (
            <a
              key={s.id}
              href={`#${s.id}`}
              className={`guide-index-item${active === s.id ? ' active' : ''}`}
              aria-current={active === s.id ? 'true' : undefined}
            >
              {s.title}
            </a>
          ))}
        </nav>

        <div className="guide-main">
          {SECTIONS.map((s) => (
            <section
              key={s.id}
              id={s.id}
              className="guide-section"
              aria-labelledby={`${s.id}-h`}
              ref={(el) => { refs.current[s.id] = el; }}
            >
              <h2 id={`${s.id}-h`} className="guide-section-title">{s.title}</h2>
              <p className="guide-lead">{s.lead}</p>

              <dl className="guide-blocks">
                {s.blocks.map((b) => (
                  <div className="guide-block" key={b.term}>
                    <dt>{b.term}</dt>
                    <dd>{b.def}</dd>
                  </div>
                ))}
              </dl>

              {s.tip && <p className="guide-tip">{s.tip}</p>}
            </section>
          ))}

          <p className="guide-foot">
            Still stuck? Ask your administrator, or read the platform docs for the technical detail.
          </p>
        </div>
      </div>
    </div>
  );
}

export default Help;
