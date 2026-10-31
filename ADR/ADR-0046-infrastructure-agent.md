<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0046 — The infrastructure agent explains platform alerts to operators

- **Status:** Accepted
- **Date:** 2026-10-31
- **Parent:** [ADR-0045](ADR-0045-agent-runtime.md) (the agent contract)
- **Related:** [ADR-0016](ADR-0016-observability-and-monitoring-integration.md) (dec 1, 4, 5: the
  operator's stack, its receivers, tenant data kept off it), [ADR-0031](ADR-0031-alerting-http-surface-single-owner.md)
  (dec 1: tenant alerts are the gateway's), [ADR-0032](ADR-0032-backup-and-disaster-recovery.md) (dec 1:
  store classes), [ADR-0043](ADR-0043-platform-switch-operator-control.md) (dec 1: platform scope is
  the operator's), [ADR-0009](ADR-0009-kubernetes-deployment-and-packaging.md) (dec 4, 6)

## Context

A platform alert names a symptom; working out what happened takes metrics, logs and the runbooks, and
the operator reading the alert is often not the one who knows them. The platform's alert rules, metrics
and logs live in the operator's monitoring stack, beside the platform on a single host and installed by
the operator on a cluster; its alert router has no receiver as shipped. Logs carry tenant identifiers
and user addresses even though tenant data stays off that stack.

## Decision

1. **The agent is triggered by the operator's alert router, through a receiver the operator adds, and
   by nothing else.** It writes into the operator's own stack, so it needs no operator address; the
   receiver authenticates with a shared secret.
2. **Its evidence is the platform plane only:** metrics around the alert, error logs of the alert's
   targets, the alert router's own alerts, and the matching runbook rows. Tenant alerts and stores stay
   out of reach (ADR-0016 dec 5, ADR-0031 dec 1).
3. **A note states what happened, the impact, the likely cause with its evidence, and the next step from
   the runbook.** A cause the evidence does not support is reported as unknown.
4. **The note is written to the log store and as an annotation on the operator's dashboards, with a
   provisioned dashboard listing them.** Notes are derivable, not backed up (ADR-0032 dec 1).
5. **The agent is read-only.** It acts on nothing, and remediation is not part of it.
6. **It serves platform operators only and is never exposed to tenants.**
7. **Its reach is the metrics store, the log store, the alert router, the dashboard annotations and the
   model endpoint.** Its credentials are the receiver's secret and a token that can only annotate; no
   database, broker, container runtime or tenant credential.
8. **It redacts tenant identifiers, user addresses, network addresses and credential-shaped strings,
   into stable placeholders.** The same identifier gets the same placeholder within a note, so the
   evidence stays correlated.
9. **The runbooks' symptom, cause and fix tables are a contract the agent reads.** A guard keeps their
   shape.
10. **It ships for both deployment shapes, off by default.** Endpoints are configuration, so on a
    cluster it reaches the operator's stack, and its dashboard is an integration object (ADR-0016 dec 1).

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| 1 | Scanning logs on a schedule for problems | The rules already detect; scanning duplicates them with a model |
| 1 | A receiver enabled by default | ADR-0016 rejects a default receiver; the operator decides |
| 1 | An unauthenticated receiver relying on the network alone | Anything on that network could make it spend and write |
| 2 | The gateway's alert history as evidence | Tenant data (ADR-0031 dec 1) |
| 2 | Database queries as evidence | Puts tenant data and a database credential in the agent |
| 4 | A chat channel or email as the output | An operator address and an outside service the platform cannot know |
| 4 | Notes only in the product console | The console is tenant-facing; the operator works in the monitoring stack |
| 4 | A database table for notes | A store and credential for text that is derivable |
| 4 | Notes as authoritative records, backed up | They can be regenerated and are advice, not record |
| 5 | Restarting or scaling services from the note | An unreviewed model acting on production |
| 6 | Tenant-facing explanations | Platform-plane evidence describes every tenant at once |
| 8 | Sending logs unredacted to a local model | A local endpoint is still another host (ADR-0045 dec 6) |
| 9 | Retrieval over free text by similarity | The runbooks already hold structured rows; similarity adds a store and guesses |
| — | One record for the runtime and this agent | The runtime binds agents this one does not describe |

## Consequences

**Positive**
- An alert arrives with an explanation and a next step, on the dashboards the operator already uses.
- The agent's reach is the operator's monitoring plane, so a compromised agent exposes no tenant store.

**Negative**
- Recent changes are not in the evidence, so a cause introduced by a deploy is seen only through its
  symptoms.
- A note can be wrong; it is advice beside the alert, not a diagnosis.
- Runbook tables gain a consumer, so their shape can no longer change freely.

## Deferred

| Open question | Settled by |
|---|---|
| Recent changes as evidence | The platform reporting its running release as a metric |
| An on-demand explanation without an alert | The first request for one |
| A chat channel as a second output | An operator who supplies one |

## References

- `infrastructure/alertmanager/alertmanager.yml`: the receiver the operator adds.
- [docs/operations/monitoring.md](../docs/operations/monitoring.md): the platform rules and runbooks.
