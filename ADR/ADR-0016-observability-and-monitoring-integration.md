<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0016 — Observability and monitoring integration

- **Status:** Accepted
- **Date:** 2026-06-27; changed 2026-11-06
- **Parent:** ADR-0001
- **Related:** [ADR-0009](ADR-0009-kubernetes-deployment-and-packaging.md) (self-contained chart; deferred monitoring here), [ADR-0003](ADR-0003-tenant-to-schema-resolution.md) (the tenant-isolated path alerts stay on), [ADR-0004](ADR-0004-api-authentication-sessions-and-transport.md) (external traffic only over the TLS edge; sessions), [ADR-0043](ADR-0043-platform-switch-operator-control.md) (the superuser as the one platform-wide authority), [ADR-0014](ADR-0014-notebook-hub-single-sign-on.md) (the same sign-on pattern for the hub), [ADR-0026](ADR-0026-release-model-and-the-compose-smoke-gate.md) (dec 9: a release names images already built)

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
   turns that on, signed on as decision 7 sets; every other console stays on the loopback.**
   External traffic is TLS at the edge only (ADR-0004 dec 1), and the operator decides whether the
   operator plane is reachable wherever the edge is. The operator may limit it to named source
   addresses; with none named, any source reaches the sign-on.
7. **The dashboards at the edge sign on from a platform superuser's session, by an assertion the
   gateway signs for them alone.** The superuser is the platform's one cross-tenant authority (ADR-0043
   dec 1), the operator who may read every service's logs; any other session is refused at the edge.
   The dashboards accept an identity only under that signature, short-lived and addressed to them,
   never from a header that network position vouches for; it names the platform user by a stable id, and
   the role it confers is below the dashboards' own administrator, whose login stays on the loopback. A
   write reaches them only from this origin.
8. **Every image the platform builds that runs platform code and serves metrics reports the source
   revision it was built from, as a constant series; a process whose metrics cannot carry the series
   serves it alone on a port of its own.** A deploy then shows in the same store, on the same time axis,
   as the symptoms it may cause. The revision is fixed when the image is built, so the series names what
   runs, not what a deployment meant to run; an image the platform does not build has no such revision.
   A long-lived platform worker that serves no metrics and no HTTP API reports it too, alone on an
   internal port of its own: nothing else rides on that listener, and it is no part of the worker's
   liveness.

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
| 7 | The dashboards' own login at the edge | A second, long-lived credential exposed wherever the edge is, beside a session that already names the operator |
| 7 | The dashboards trusting an identity header the edge sets | Every service sharing their network could assert any operator; network position is not identity (ADR-0014 dec 3 refuses it for the hub) |
| 7 | The dashboards on a network only the edge reaches | Ties trust to addresses fixed in the deployment, and the loopback port would still honour the header |
| 7 | Any authenticated platform user | A tenant's authority ends at its tenant (ADR-0004 dec 5); the dashboards show every service |
| 7 | The superuser as the dashboards' administrator | A stolen session would also rewrite data sources and users; operating needs reading and exploring only |
| 7 | The assertion naming the user by e-mail | The dashboards match an e-mail against their own accounts first, their administrator's included, and an e-mail can change hands |
| 7 | A write accepted on the session alone | The dashboards hold no session of their own, so their forgery check never runs; a same-site page could write as the superuser |
| 7 | An assertion handed to the browser | A token the page can read outlives the request it was minted for; the edge asks for it per request and forwards it |
| 8 | The release name as the reported version | A release is named after its images are built and names them unchanged (ADR-0026 dec 9), so an image cannot carry it |
| 8 | The revision read from the deployment when a service starts | States what the deployment meant to run; a service rebuilt or replaced alone would report another image's revision |
| 8 | A deploy event recorded by the deploy tool | Every way of deploying would have to record it, and an image swapped outside them would leave no trace |
| 8 | Container restarts as the only sign of a deploy | A restart is not a change; a crash and a deploy look the same |
| 8 | Every service that serves metrics, including the stream-processing cluster's stock master | No build fixes its revision; one stamped from the deployment is the deployment-read revision rejected above |
| 8 | The stream-processing workers reporting the series | A worker runs the stock worker process and executes only code its driver ships, so the driver's series already names that code's revision |
| 8 | The stream processors left without the series | Their symptoms (consumer lag, dead letters, missing windows) are the ones a deploy most needs to be read against |
| 8 | A worker that serves no metrics left without the series | The alert worker's symptoms (consumer lag, a stalled tenant-status stream) are alerted on, and its deploys would show only as restarts |
| 8 | Such a worker serving its full metrics beside the series | A general metrics surface for a process nothing reads it from, and the publisher metric ADR-0033 dec 3 rejects |
| 8 | The revision in the worker's heartbeat file | A per-container liveness probe reads it, so it never reaches the metrics store, and it ties the revision to liveness |
| 8 | The revision as a log line | A line parsed back into a label breaks silently when either side changes |
| 8 | The revision pushed to a push gateway | A pushed series outlives its process, so it states what ran, not what runs |
| 8 | A plugin in the stream processor's runtime emitting the series among its own metrics | Adds a second build toolchain and its supply chain, couples to the processor's upgrades, and still misses the master |
| 8 | The revision encoded in a metric name of the processor's own metrics and relabelled at the scrape | Those metrics carry no free labels; a name parsed back into a label breaks silently when either side changes |

## Consequences

**Positive**
- The platform is observable wherever an operator-compatible stack exists, including managed ones.
- Dashboards and rules grow without touching service definitions.
- A single-host operator reads the dashboards in a browser over the platform's own TLS, with no second
  login and no dashboard password exposed at the edge.

**Negative**
- Self-hosted operators install the monitoring stack once per cluster and enable the integration.
- Without the operator, only raw exporter metrics are available.
- A stolen superuser session also reads every service's metrics and logs while it lives.
- Every dashboard request through the edge costs one session check at the gateway.
- The single host holds one more signing key, generated at install and rotated by replacing it.
- A service that serves no metrics of its own shows a deploy only as a restart.
- The stream-processing cluster's master and workers report no revision; a change to their images shows only through its symptoms.
- A stream-processing driver, and a worker that serves no metrics, opens one more port, for the series alone.
- Every deploy of a new commit changes every reporting service's revision, whether its code changed or not.

## Deferred

| Open question | Settled by |
|---|---|
| A tenant-scoped alert stream for a future consumer | The first consumer that needs one |

## References

- [docs/operations/monitoring.md](../docs/operations/monitoring.md): toggles, role, install steps.
- [deploy/observability/README.md](../deploy/observability/README.md): installing the cluster stack.
- `infrastructure/prometheus/rules/platform.yml`: the platform rules, decision 1.
- `deploy/helm/industryflow/templates/observability-*.yaml`: decisions 1–2.
- `services/frontend/grafana.conf.template`: decisions 6–7.
- `services/api_gateway/routers/dashboards_assertion.py`: decision 7.
- `tests/config_guards/test_build_info.py`: decision 8.
