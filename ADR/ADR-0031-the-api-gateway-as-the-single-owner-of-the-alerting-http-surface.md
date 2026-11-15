<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0031 — The API gateway as the single owner of the alerting HTTP surface

- **Status:** Accepted
- **Date:** 2026-10-11; changed 2026-11-06
- **Parent:** [ADR-0003](ADR-0003-tenant-to-schema-resolution.md) (one shared tenant-resolution
  implementation)
- **Related:** [ADR-0016](ADR-0016-observability-and-monitoring-integration.md)

## Context

1. Alert rules and alert history are a resource that browser, client library and notebooks reach
   over HTTP.
2. A second HTTP surface for the same resource, inside the alerting service, diverges from the
   first: operations answer to different verbs, capabilities exist on one side only, and session
   verification and tenant discovery are reimplemented, which ADR-0003 forbids.
3. Every change to alerting then has to be made twice, and the copy no caller exercises is the one
   that drifts.

## Decision

1. **The API gateway is the single HTTP surface for alert rules and alert history,** including
   acknowledgement, operator labels and precision. Every human and service caller reaches alerting
   over HTTP through it, behind the one edge (ADR-0004), with the one tenant resolution (ADR-0003).
   Rests on: 1, 2.
2. **The alerting service exposes no HTTP API of its own.** It consumes readings, evaluates rules
   and writes alerts and recommendations (ADR-0005, ADR-0021, ADR-0022), and has no separate HTTP
   deployable or image. Its one listener serves the build revision alone (ADR-0016 dec 8) and is no
   HTTP capability in the sense of decision 3.
   Rests on: 2, 3.
3. **An HTTP capability alerting needs in future is added to the gateway's surface,** not to a
   separate endpoint.
   Rests on: 2, 3.

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| 1 | The alerting service owns its HTTP surface; the gateway proxies to it | Moves tenant resolution and session verification into a second service, and adds a hop for no capability the gateway lacks |
| 2 | No listener on the worker at all | A deploy of the worker would show only as a restart (ADR-0016 dec 8) |
| 2 | Keep both surfaces and reconcile them | Two owners of one resource is the condition that produces drift; reconciling once does not stop it recurring |

## Consequences

**Positive**
- One tenant-resolution path and one session verification for alerting.
- One verb per operation; changes to alerting land once.
- One fewer deployable to build, scan, scrape, secure and keep healthy.

**Negative**
- A read only a separate surface would offer exists only once a caller needs it on the gateway.
- The gateway's availability is alerting's HTTP availability; detection does not depend on it.

## Deferred

| Open question | Settled by |
|---|---|
| None | — |

## References

- [Alerting](../docs/architecture/alerting.md): manual of decisions 1-3.
