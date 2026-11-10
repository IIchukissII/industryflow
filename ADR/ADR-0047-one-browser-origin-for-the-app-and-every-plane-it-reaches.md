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
  (dec 6: where the hub is served), [ADR-0030](ADR-0030-admission-of-externally-authored-models.md)
  (dec 10: the upload plane by sub-path), [ADR-0016](ADR-0016-observability-and-monitoring-integration.md)
  (dec 7: dashboards written only from this origin), [ADR-0013](ADR-0013-experiment-tracking-and-model-registry-multi-tenancy.md)
  (dec 3: artifact bytes between caller and store), [ADR-0048](ADR-0048-notebook-environments-are-served-from-an-origin-of-their-own.md)
  (the notebook environments' own origin)

## Context

The browser session is a pair of cookies the backend sets, scoped to one site, with a CSRF token the
page must read and echo, and a live socket admitted by its origin. Each of these holds only while the
page and the APIs it calls share an origin.

Several planes are reached from the browser besides the API: the model upload plane, the dashboards
and the notebook hub. An alternative rejected for the upload plane rests on the one origin, yet no
decision records it. The notebook environments run code the platform does not trust, and script
served from the platform's origin acts with the user's session.

## Decision

1. **The browser app is a single-page app served by the platform's edge, on the same origin as every
   API it calls.** The session cookies, the CSRF token and the socket origin check (ADR-0004 dec 3, 9)
   then hold without a cross-origin exception, and the app makes no cross-origin request; the origin
   allowlist (ADR-0004 dec 6) names this origin for the socket check.
2. **Every other plane the browser reaches is a sub-path of that origin, behind the same edge, except
   the notebook environments (ADR-0048).** The upload plane and the dashboards serve only
   platform-authored content and share the session, the certificate and the frame policy of the one
   origin. A store the browser writes under a grant is a sub-path too: the edge forwards the
   bytes, which still pass no plane (ADR-0013 dec 3), and the store exposes no cross-origin policy.

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| 1 | The app on its own origin, calling the API cross-origin | The page could not read the CSRF token the API sets, every call needs a credentialed cross-origin policy, and a cross-site origin also needs `SameSite=None` cookies |
| 1 | Tokens kept in browser storage and sent as bearer headers from any origin | Script-readable tokens, against httpOnly cookies (ADR-0004 dec 3) |
| 1 | Pages rendered by the API service | A user interface inside the API service, and a second edge path for its assets |
| 2 | Each plane on its own subdomain | Each needs its own certificate name, cookie scope and frame policy; only a plane that runs untrusted content gains by it (ADR-0048) |
| 2 | The notebook hub under a path of this origin | Kernel-produced script would act with the user's session (ADR-0048) |
| 2 | Planes reached directly, without the edge | A second public surface per plane, against one edge (ADR-0009 dec 5) |

## Consequences

**Positive**
- One certificate, one cookie scope and no cross-origin policy serve the app and every plane on its origin.
- Development serves the same paths from one origin, so the session behaves as in a deployment.

**Negative**
- Every plane on the origin shares its trust: script injected into any of them runs with the page's
  standing, CSRF token included.
- The edge's route table carries every plane's path, and each plane must serve under a path prefix.
- The app's static assets are served by the same edge as the APIs.

## Deferred

| Open question | Settled by |
|---|---|

## References

- [Frontend](../services/frontend/README.md): manual of decisions 1, 2.
