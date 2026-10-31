<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0016 — Observability and monitoring integration

- **Status:** Accepted
- **Date:** 2026-06-27; changed 2026-10-31
- **Parent:** ADR-0001
- **Related:** [ADR-0009](ADR-0009-kubernetes-deployment-and-packaging.md) (self-contained chart; deferred monitoring here), [ADR-0003](ADR-0003-tenant-to-schema-resolution.md) (the tenant-isolated path alerts stay on), [ADR-0004](ADR-0004-api-authentication-sessions-and-transport.md) (external traffic only over the TLS edge)

## Context

Monitoring is cluster infrastructure with its own lifecycle and operator; managed deployments often use
a provider's backend. The chart must stay self-contained and lintable offline (ADR-0009). There are two
alert planes: platform health for operators, and tenant sensor alerts, which are tenant data and must
not leave the tenant-isolated path.

## Decision

1. **The application chart emits integration objects, not a monitoring backend.** It renders scrape
   targets generated from the service list, platform alert rules on generic series, and dashboards
   labelled for import, the rules and dashboards single-sourced with those the single-host deployment
   loads; the operator-dependent objects default off. Every rule selects only series labelled as the
   platform's own, which both deployments attach to every target.
2. **Data-store exporters ship with the chart; cluster-generic collectors come from the monitoring
   stack.** The exporters target the platform's own database, cache and broker.
3. **The database exporter connects as a dedicated least-privilege monitoring role, in every deployment
   shape.** A superuser there would make a compromised exporter a compromised database.
4. **The monitoring backend is installed beside the chart, not in it.** Integration objects carry
   configurable labels to match the stack's selectors. On a single host the backend runs beside the
   application services, an alert router included, whose receivers the operator supplies.
5. **Tenant alerts stay on the tenant-isolated path and never pass through the shared monitoring
   stack.** An alert goes from the rules engine directly into the tenant's own store, and metrics on the
   platform plane are operational and aggregate, never per-tenant values or alert contents.
6. **On a single host, the dashboards are reached through the platform's TLS edge when the operator
   turns that on, behind the dashboards' own login; every other console stays on the loopback.**
   External traffic is TLS at the edge only (ADR-0004 dec 1), and the operator decides whether the
   operator plane is reachable wherever the edge is. The operator may limit it to named source
   addresses; with none named, any source reaches the login.

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| 1 | Separate rule sets per deployment | They drift; the single-host deployment had none, so its dead letters raised nothing |
| 1 | Rule thresholds templated from chart values | A templated copy is not one source |
| 1 | Rules unscoped, or scoped by job name | In a shared backend they fire on other stacks; job names differ between deployments |
| 3 | A superuser exporter on the Compose path, least privilege only on Kubernetes | A compromised exporter would be a compromised database whichever orchestrator runs it |
| 4 | The full stack as plain workloads inside the chart | No HA or discovery, tied to the app release, dead weight in managed deployments |
| 4 | Monitoring charts as chart dependencies | Couples releases and needs a network fetch to lint (ADR-0009) |
| 4 | A single host with rules but no alert router | A firing alert reaches no one and nothing records it |
| 4 | A default notification receiver | The platform cannot know an operator's address |
| 5 | Tenant alerts passing through a topic shared by all tenants on the way to the tenant's store | A second, shared copy of tenant data outside the tenant-isolated path |
| 5 | A shared alert topic kept for a future consumer | Every tenant's alerts on one topic with no reader |
| 6 | The dashboards' port bound on every interface | Plaintext external traffic, against ADR-0004 dec 1 |
| 6 | The dashboards at the edge by default | Exposes the operator plane, and a default administrator credential, wherever the edge is reachable without the operator choosing it |
| 6 | Dashboards reached only through a tunnel to the loopback | An operator cannot use them from a browser alone |
| 6 | Source addresses limited by default | The platform cannot know an operator's addresses |
| 6 | Sign-on to the dashboards from the platform session | No platform role maps to the operator who may read every service's logs; deferred below |

## Consequences

**Positive**
- The platform is observable wherever an operator-compatible stack exists, including managed ones.
- Dashboards and rules grow without touching service definitions.
- A single-host operator reads the dashboards in a browser over the platform's own TLS.

**Negative**
- Self-hosted operators install the monitoring stack once per cluster and enable the integration.
- Without the operator, only raw exporter metrics are available.
- With the dashboards at the edge, their login is a second credential exposed wherever the edge is.

## Deferred

| Open question | Settled by |
|---|---|
| A tenant-scoped alert stream for a future consumer | The first consumer that needs one |
| Sign-on to the single host's dashboards from the platform session | A platform operator role |

## References

- [docs/operations/monitoring.md](../docs/operations/monitoring.md): toggles, role, install steps.
- [deploy/observability/README.md](../deploy/observability/README.md): installing the cluster stack.
- `infrastructure/prometheus/rules/platform.yml`: the platform rules, decision 1.
- `deploy/helm/industryflow/templates/observability-*.yaml`: decisions 1–2.
- `services/frontend/grafana.conf.template`: decision 6.
