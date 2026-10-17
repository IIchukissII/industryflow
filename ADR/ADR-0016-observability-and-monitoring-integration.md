<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0016 — Observability and monitoring integration

- **Status:** Accepted
- **Date:** 2026-06-27; changed 2026-10-17
- **Parent:** ADR-0001
- **Related:** [ADR-0009](ADR-0009-kubernetes-deployment-and-packaging.md) (self-contained chart; deferred monitoring here), [ADR-0003](ADR-0003-tenant-to-schema-resolution.md) (the tenant-isolated path alerts stay on)

## Context

Monitoring is cluster infrastructure with its own lifecycle and operator; managed deployments often use
a provider's backend. The chart must stay self-contained and lintable offline (ADR-0009). There are two
alert planes: platform health for operators, and tenant sensor alerts, which are tenant data and must
not leave the tenant-isolated path.

## Decision

1. **The application chart emits integration objects, not a monitoring backend.** It renders scrape
   targets generated from the service list, platform alert rules on generic series, and dashboards
   labelled for import, single-sourced from the Compose dashboards; the operator-dependent objects
   default off.
2. **Data-store exporters ship with the chart; cluster-generic collectors come from the monitoring
   stack.** The exporters target the platform's own database, cache and broker.
3. **The database exporter connects as a dedicated least-privilege monitoring role, in every deployment
   shape.** A superuser there would make a compromised exporter a compromised database.
4. **The monitoring backend is installed beside the chart, not in it.** Integration objects carry
   configurable labels to match the stack's selectors.
5. **Tenant alerts stay on the tenant-isolated path and never pass through the shared monitoring
   stack.** An alert goes from the rules engine directly into the tenant's own store, and metrics on the
   platform plane are operational and aggregate, never per-tenant values or alert contents.

## Alternatives

| Alternative | Why rejected |
|---|---|
| The full stack as plain workloads inside the chart | No HA or discovery, tied to the app release, dead weight in managed deployments |
| Monitoring charts as chart dependencies | Couples releases and needs a network fetch to lint (ADR-0009) |
| A shared alert topic kept for a future consumer | Every tenant's alerts on one topic with no reader |

## Consequences

**Positive**
- The platform is observable wherever an operator-compatible stack exists, including managed ones.
- Dashboards and rules grow without touching service definitions.

**Negative**
- Self-hosted operators install the monitoring stack once per cluster and enable the integration.
- Without the operator, only raw exporter metrics are available.

## Deferred

| Open question | Settled by |
|---|---|
| A tenant-scoped alert stream for a future consumer | The first consumer that needs one |

## References

- [docs/operations/monitoring.md](../docs/operations/monitoring.md): toggles, role, install steps.
- [deploy/observability/README.md](../deploy/observability/README.md): installing the cluster stack.
- `deploy/helm/industryflow/templates/observability-*.yaml`: decisions 1–2.
