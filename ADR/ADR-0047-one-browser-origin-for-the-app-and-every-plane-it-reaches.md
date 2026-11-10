<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0047 — One browser origin for the app and every plane it reaches

- **Status:** Accepted
- **Date:** 2026-11-10
- **Parent:** [ADR-0004](ADR-0004-api-authentication-session-model-and-transport-security.md) (dec 3, 9:
  the session cookies, the CSRF token and the socket origin check that need one origin)
- **Related:** [ADR-0009](ADR-0009-kubernetes-deployment-packaged-with-helm.md) (dec 5: the cluster
  ingress is the TLS edge), [ADR-0014](ADR-0014-notebook-hub-single-sign-on-from-the-platform-session.md)
  (dec 6: the hub under the platform's origin), [ADR-0030](ADR-0030-admission-of-externally-authored-models.md)
  (dec 10: the upload plane by sub-path), [ADR-0016](ADR-0016-observability-and-monitoring-integration.md)
  (dec 7: dashboards written only from this origin), [ADR-0013](ADR-0013-experiment-tracking-and-model-registry-multi-tenancy.md)
  (dec 3: artifact bytes between caller and store)

## Context

The browser session is a pair of cookies the backend sets, scoped to one site, with a CSRF token the
page must read and echo, and a live socket admitted by its origin. Each of these holds only while the
page and the APIs it calls share an origin.

Several planes are reached from the browser besides the API: the notebook hub, the model upload plane
and the dashboards. Their decisions already place each one on the platform's own origin, and an
alternative rejected for the upload plane rests on it, yet no decision records the origin itself.

## Decision

1. **The browser app is a single-page app served by the platform's edge, on the same origin as every
   API it calls.** The session cookies, the CSRF token and the socket origin check (ADR-0004 dec 3, 9)
   then hold without a cross-origin exception, and the app makes no cross-origin request.
2. **Every other plane the browser reaches is a sub-path of that origin, behind the same edge.** The
   hub, the upload plane and the dashboards share the session, the certificate and the frame policy of
   the one origin. A store the browser writes under a grant is a sub-path too: the edge forwards the
   bytes, which still pass no plane (ADR-0013 dec 3), and the store exposes no cross-origin policy.

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| 1 | The app on its own origin, calling the API cross-origin | The cookies would need `SameSite=None` and a credentialed cross-origin policy, and the page could not read the CSRF token the API sets |
| 1 | Tokens kept in browser storage and sent as bearer headers from any origin | Script-readable tokens, against httpOnly cookies (ADR-0004 dec 3) |
| 1 | Pages rendered by the API service | A user interface inside the API service, and a second edge path for its assets |
| 2 | Each plane on its own subdomain | Each needs its own certificate name, cookie scope and frame policy, and the hub may be framed only by the platform's origin (ADR-0014 dec 6) |
| 2 | Planes reached directly, without the edge | A second public surface per plane, against one edge (ADR-0009 dec 5) |

## Consequences

**Positive**
- One certificate, one cookie scope and no cross-origin policy serve the app and every plane.
- Development serves the same paths from one origin, so the session behaves as in a deployment.

**Negative**
- Every plane shares the origin's trust: script injected into any of them runs with the page's
  standing, CSRF token included.
- The edge's route table carries every plane's path, and each plane must serve under a path prefix.
- The app's static assets are served by the same edge as the APIs.

## Deferred

| Open question | Settled by |
|---|---|

## References

- [Frontend](../services/frontend/README.md): manual of decisions 1, 2.
