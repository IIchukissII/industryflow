<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0007: Public-key infrastructure — the IndustryFlow device certificate authority

- **ID:** ADR-0007
- **Status:** Accepted
- **Date:** 2026-06-26
- **Project:** IndustryFlow
- **Parent:** ADR-0001
- **Companions:** ADR-0002 (ingestion authentication & device identity), ADR-0004 (API authentication, sessions & transport)
- **Related (IndustryGrow):** ADR-0007 (PKI and secure-element identity), ADR-0004 rev 1 (gateway host hardening)

## Context and problem

ADR-0002 decided that device/gateway ingestion authenticates with mTLS, using client certificates issued by IndustryFlow's own certificate authority, with the tenant (`company_id`) and `device_id` carried in the certificate; it deferred the PKI itself. ADR-0004 (rev 1) decided that the browser-facing API is served over HTTPS with a deployment-keyed edge server certificate (ADR-0004 dec 8: public ACME when managed, an internal CA when self-hosted), and explicitly noted that this server certificate is a **different** PKI concern from the device CA. Both ADRs therefore point at a PKI that has not yet been specified: who the root of trust is, how a device's identity is encoded and bound to a tenant, how a device enrolls, how long a certificate lives, and how a certificate is revoked.

The producers this CA serves are not ordinary software clients. On the IndustryGrow gateway — the first deployment — the device's private key lives in an **ATECC secure element**: it is generated on-device and is non-exportable (IndustryGrow ADR-0007, ADR-0004 rev 1). That hardware fact constrains the PKI: the CA can never generate or hold a device's private key, because a key that exists off the device is exactly the property the secure element exists to prevent. The PKI must be built around signing certificate requests for keys it never sees.

This ADR specifies the device CA. It does not re-decide that mTLS is used (ADR-0002) or that the browser edge uses ADR-0004's edge server certificate (ADR-0004 dec 8); it decides the certificate authority behind the device side of that trust.

## Decision drivers

- **Device private keys never leave the device.** The CA signs certificate requests; it does not create or store device keys. This is dictated by the secure-element identity model and is non-negotiable.
- **Compromise must be containable.** Compromise of a day-to-day signing key must not require re-establishing the trust anchor on every gateway in the field.
- **Tenant identity must be verifiable and unspoofable from the certificate.** ADR-0003 resolves a request's tenant from a verified identity; for devices that identity is a certificate field, so the field must be authentic and machine-parseable.
- **Revocation must be possible.** A stolen, compromised, or decommissioned device must be able to lose access before its certificate would naturally expire.
- **It must operate unattended.** Enrollment and renewal must work for field devices with no human at the keyboard.
- **Client trust and server trust are separate.** The device CA authenticates clients *to* IndustryFlow; the browser-facing edge certificate authenticates IndustryFlow *to* browsers. The two trust directions must not be conflated.

## Decision

1. **Two-tier hierarchy: an offline root CA and an online issuing intermediate.** The root CA is kept offline and signs only intermediate certificates; an online issuing intermediate signs device certificates. If the issuing intermediate is compromised, it is revoked and replaced at the root without re-rooting trust on every device. The root is the long-lived anchor distributed to the verifying proxy; the intermediate is the operational signer.

2. **Devices generate their own keys; the CA signs CSRs and never holds a private key.** A device generates its keypair inside its secure element, emits a certificate signing request (CSR) for the public key, and the CA issues a certificate binding that public key to the device's identity. The CA neither generates nor stores device private keys.

3. **Tenant and device identity are encoded in a URI SAN, not the Common Name.** The certificate carries the tenant `company_id` and the `device_id` in a Subject Alternative Name URI (a scheme such as `industryflow:tenant/<company_uuid>/device/<device_id>`). The verified tenant that ADR-0003 consumes is read from this SAN. The Common Name, if present, is a human label only and is never used for authorization (CN-as-identity is deprecated).

4. **A device is bound to its tenant at issuance.** Enrollment authorizes a CSR for a specific `company_id` before the certificate is issued; a device cannot self-assert its tenant. The binding is an issuance-time decision (an operator or an automated provisioning step), so the tenant in the SAN is one the CA vouched for, not one the device chose.

5. **Certificates are bounded-lifetime and rotated before expiry.** Device certificates have a finite validity and are renewed ahead of expiry by the device generating a fresh key/CSR in its secure element. Bounded lifetime limits the exposure window of any single certificate; the concrete duration and renewal cadence are an implementation detail (deferred).

6. **Revocation is supported and checked at verification.** The CA publishes revocation status (a CRL and/or OCSP), and the terminating reverse proxy (ADR-0002) checks revocation when it verifies a client certificate. A decommissioned or compromised device is revoked and loses access without waiting for its certificate to expire.

7. **The device CA is separate from the browser-facing server certificate.** The proxy presents the browser-facing server certificate (ADR-0004 dec 8 — public ACME when managed, an internal CA when self-hosted) to browsers and verifies device client certificates against this device CA. The two chains are distinct: one authenticates us to browsers, the other authenticates devices to us. Neither is used for the other's purpose.

## Alternatives considered

**A. Single-tier CA (root signs device certificates directly).** *Rejected:* the root key would have to be online to issue device certificates, so its compromise is catastrophic and unrecoverable — every gateway trusts it directly, and replacing it means re-rooting the entire fleet. The offline-root/online-intermediate split makes day-to-day signing-key compromise recoverable.

**B. Encode identity in the Common Name instead of a SAN.** *Rejected:* CN-as-identity is deprecated (RFC 6125); the SAN is the correct, multi-valued, machine-parseable field, and a structured URI SAN expresses tenant + device cleanly where a single CN string cannot.

**C. Per-tenant intermediate CAs (one issuing intermediate per company).** *Rejected as the default, flagged for review:* it gives stronger isolation — revoking a tenant is revoking its intermediate, and an intermediate compromise is scoped to one tenant — but it multiplies operational cost (an intermediate, a CRL/OCSP responder, and a key-management lifecycle per tenant) at a scale that does not yet justify it. A single issuing intermediate with the tenant in each certificate's SAN suffices now; per-tenant intermediates can be adopted later if isolation requirements grow. **This is the main open fork in this ADR.**

**D. The CA generates device keypairs and provisions them onto devices.** *Rejected:* it defeats the secure element's non-exportable-key guarantee — a key the CA generated is a key that existed off the device. Decision 2 exists precisely to forbid this.

**E. No revocation; rely on short-lived certificates alone.** *Rejected:* a compromised device retains access for the remainder of its certificate's validity, which is unacceptable for theft or decommission. Short lifetimes bound exposure but do not replace the ability to cut a device off immediately.

**F. Reuse the browser-facing edge server-certificate path for device authentication.** *Rejected:* opposite trust direction. Devices are clients we authenticate, not servers a browser trusts; the edge server certificate (ADR-0004 dec 8) says nothing about which tenant a device belongs to.

## Consequences

### Positive

- Device private keys never leave hardware, so a device credential cannot be copied off the device — the property ADR-0002 depends on.
- The tenant a device belongs to is cryptographically vouched for by the CA and read from a verified SAN, so it cannot be spoofed by the device (closes the trust gap ADR-0003 relies on for ingestion).
- An issuing-intermediate compromise is recoverable without re-rooting the fleet, and revocation gives immediate cutoff for theft or decommission.
- Client trust (device CA) and server trust (the browser-facing edge certificate) stay cleanly separated, so neither can be misused for the other.

### Negative

- Operating a CA is real, ongoing work: an offline root with a key-custody ceremony, an online issuing intermediate, and revocation publication (CRL/OCSP) infrastructure.
- An enrollment/provisioning workflow that binds a device to a tenant at issuance must be built and operated; it is the gate on which the whole tenant-identity guarantee rests.
- Revocation checking adds latency and an availability dependency at the verifying proxy.
- Unattended fleets need automated certificate rotation; a device that fails to renew loses access.
- With a single issuing intermediate (rejecting alternative C for now), tenant isolation is at the certificate level, not the CA level: an intermediate compromise spans tenants. This is an accepted trade-off pending the alternative-C decision.

## Deferred decisions

- **Per-tenant intermediates (alternative C).** Whether to move from one issuing intermediate to per-tenant intermediates as scale/isolation needs grow.
- **Enrollment protocol.** Whether enrollment uses EST (RFC 7030), SCEP, or a manual/custom CSR flow, and how the provisioning step authorizes a CSR for a tenant.
- **Certificate lifetimes and rotation cadence.** Concrete validity periods and renewal timing for device certificates.
- **Revocation mechanism.** CRL vs OCSP (vs OCSP stapling), its hosting, and freshness requirements.
- **Root-key custody.** Where the offline root key lives (HSM, air-gapped media) and the signing-ceremony procedure.
- **SAN URI scheme.** The exact URI structure encoding tenant and device, coordinated with ADR-0003's tenant resolution.

## References

- ADR-0002 — ingestion authentication & device identity; establishes mTLS with certificates from this CA and the verifying reverse proxy.
- ADR-0004 — API authentication, sessions & transport; the browser-facing edge server certificate (dec 8) that this ADR keeps distinct from the device CA.
- ADR-0003 — tenant→schema resolution; consumes the verified tenant identity encoded by decision 3.
- IndustryGrow ADR-0007 — PKI and secure-element identity; the device-side counterpart whose non-exportable-key model constrains decisions 1–2.
- RFC 5280 (X.509 PKI), RFC 6125 (identity in certificates), RFC 7030 (EST) — standards underpinning the hierarchy, SAN identity, and enrollment choices.
