<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0031 — The alerting HTTP surface has one owner: the API gateway

- **ID:** ADR-0031
- **Status:** Accepted
- **Date:** 2026-10-11
- **Project:** IndustryFlow
- **Parent:** [ADR-0003](ADR-0003-tenant-to-schema-resolution.md) (tenant resolution is one shared implementation that every service uses; a second HTTP surface carried a second one)
- **Companions:** [ADR-0004](ADR-0004-api-authentication-sessions-and-transport.md) (one edge, one session model), [ADR-0022](ADR-0022-concept-drift-operator-feedback.md) (operator labels and precision, which exist on only one of the two surfaces)
- **Related:** [ADR-0005](ADR-0005-kafka-delivery-semantics.md) (the detector consumes and writes alerts; it needs no HTTP surface), [ADR-0016](ADR-0016-observability-and-monitoring-integration.md) (each deployable is a scrape target), [ADR-0021](ADR-0021-model-drift-monitoring.md) (drift alerts share the alert lane)

## Context and problem

Alert rules and alert history were served over HTTP by two deployables. One is the API gateway, which
sits behind the platform's edge. The other is an HTTP API inside the alerting service, published on
its own port and reached by nothing the platform ships. Neither the browser, the client library, the
notebooks nor the detector calls it.

The two had diverged. The same operation answered to different verbs. Operator labels and precision
(ADR-0022) existed on one surface only, and a statistics read existed on the other only. More
seriously, the second surface verified the session token with its own code and re-discovered the
tenant by looking the user up. That is a second tenant-resolution implementation, which ADR-0003
forbids. Two surfaces for one resource also means every change to alerting has to be made twice, and
the copy nobody exercises is the one that drifts.

ADR-0000 records service boundaries as decisions. Which deployable owns a resource's HTTP surface is
such a boundary, so the retirement is recorded here rather than done silently.

## Decision

1. **The API gateway is the single HTTP surface for alert rules and alert history,** including
   acknowledgement, operator labels and precision. Every human and service caller reaches alerting
   over HTTP through it, behind the one edge (ADR-0004), with the one tenant resolution (ADR-0003).
2. **The alerting service exposes no HTTP API of its own.** It keeps its domain role: consuming
   readings, evaluating rules, writing alerts and recommendations (ADR-0005, ADR-0021, ADR-0022).
   Its separate HTTP deployable is retired, together with its port, image and scrape target.
3. **A capability the alerting service must offer over HTTP in future is added to the gateway's
   surface,** not by reopening a separate endpoint. A second owner of the same resource is a drift
   waiting to happen, and the copy that drifts is the one no caller exercises.

## Consequences

### Positive

- One tenant-resolution path for alerting; the divergent copy and its private token handling are gone.
- One verb per operation, and changes to alerting land once.
- One fewer deployable to build, scan, scrape, secure and keep healthy.

### Negative

- A read that only the retired surface offered is gone. It is restored on the gateway only when a
  caller needs it.
- The gateway carries one more resource, so the gateway's availability is alerting's HTTP availability.
  Detection does not depend on it.

## Alternatives considered

**A. The alerting service owns its HTTP surface and the gateway proxies to it.** *Rejected:* it
moves tenant resolution and session verification into a second service (ADR-0003, ADR-0004) and adds
a hop for no capability the gateway lacks.

**B. Keep both and reconcile them.** *Rejected:* two owners of one resource is the condition that
produced the drift; reconciling them once does not stop it recurring.

## References

- ADR-0000 — decision records; service boundaries are decisions.
- ADR-0003 — tenant resolution, one shared implementation (dec 7).
- ADR-0004 — one edge, one session model.
- ADR-0022 — operator labels and precision.
