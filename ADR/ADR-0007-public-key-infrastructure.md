<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0007 — Device certificate authority

- **Status:** Accepted
- **Date:** 2026-06-26; changed 2026-10-23
- **Parent:** ADR-0001
- **Related:** ADR-0002 (device mTLS), ADR-0003 (vouched tenant), ADR-0004 (server cert), ADR-0009 (secrets), ADR-0033 (dec 4: a deleted tenant's devices revoked)

## Context

Device ingestion authenticates with client certificates from the platform's own CA (ADR-0002). Field
gateways generate a non-exportable key in a secure element, so the CA signs requests for keys it never
sees. A revoked device must lose access at once, and an expired revocation list blocks every device.

## Decision

1. **Two tiers: an offline root and an online issuing intermediate.** A compromised intermediate is
   replaced at the root without re-rooting the fleet.
2. **Devices generate their own keys; the CA signs requests and never holds a device key.**
3. **Tenant and device identity are a URI subject alternative name, never the common name.** One
   shape is issued and parsed, with a canonical tenant and a restricted device-identifier character set.
4. **A device is bound to its tenant at issuance.** An operator authorizes each request for one tenant.
5. **Certificates have a bounded lifetime and are renewed with a fresh key before expiry.**
6. **Revocation is checked at verification, by the terminating proxy.**
7. **The device CA is separate from the browser-facing server certificate.** Neither chain serves the
   other's purpose.
8. **Revocation is a list held at the verifier, re-signed where its key lives, well before expiry.**
   The intermediate's list is re-signed routinely beside the issuing key, or from outside a cluster
   that only watches its age. The root's list is re-signed only at the offline ceremony, and every
   approaching expiry is raised in advance.
9. **Beside each list the authority publishes a register of every certificate it issued, signed with the
   issuing key.** It names that list and holds no key, so a verifier with only public files can check
   that every unexpired certificate of a tenant is revoked. A tenant is revoked as a whole by one
   operator command at the authority.

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| 1 | Root signs devices directly | The root must be online; its compromise re-roots the fleet |
| 1 | One intermediate per tenant | Per-tenant key and list lifecycle not yet justified |
| 2 | CA-generated device keys | Defeats the secure element |
| 3 | Identity in the common name | Deprecated (RFC 6125) and unstructured |
| 6 | Short lifetimes, no revocation | A stolen device keeps access until expiry |
| 7 | Reusing the browser-facing server certificate path for devices | Opposite trust direction; it says nothing about a device's tenant |
| 8 | Moving the issuing key into a cluster to re-sign the list there | A plain stored secret is weaker protection than the key has outside |
| 8 | Automatic root-list re-signing | Brings the root online |
| 9 | Proof of revocation from the list alone | A list names what was revoked, never what was issued, so a missed certificate is invisible |
| 9 | An unsigned register with a published digest | Whoever can replace the register can replace its digest |
| 9 | A register that only grows at issuance | Drifts from the list it is checked against; one written with each list names it |

## Consequences

**Positive**
- A device credential cannot be copied or its tenant spoofed; an intermediate compromise is recoverable;
  revocation is immediate, and list expiry never stops ingestion unannounced.

**Negative**
- Operating a CA is ongoing work, and the tenant guarantee rests on the enrollment step.
- Issued identities, though no keys, are published beside the list.
- Revocation is an availability dependency; a device that fails to renew loses access; one
  intermediate's compromise spans tenants.

## Deferred

| Open question | Settled by |
|---|---|
| Per-tenant issuing intermediates | Isolation needs certificate identity cannot meet |
| Automated enrollment and renewal protocol | First fleet an operator cannot enroll |
| Root-key custody and ceremony | First production root |
| Online status checking | A verifier that cannot hold the list |
| In-cluster re-signing of the intermediate's list | ADR-0009's secrets backend |

## References

- [docs/operations/device-mtls.md](../docs/operations/device-mtls.md); `scripts/device-ca.sh` (lifetimes, identity scheme).
