<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0039 — One test of tenant ownership for names and object-store keys

- **Status:** Accepted
- **Date:** 2026-10-27
- **Parent:** [ADR-0003](ADR-0003-tenant-to-schema-resolution.md) (dec 7: the shared library)
- **Related:** [ADR-0013](ADR-0013-experiment-tracking-and-model-registry-multitenancy.md) (dec 2, 4:
  mediation and read paths), [ADR-0019](ADR-0019-notebook-experiment-tracking-gateway.md) (dec 5, 7:
  the namespace rule, applied by the gateway and the read paths), [ADR-0025](ADR-0025-cold-layer-historical-data-open-columnar.md)
  (dec 5: tenant isolation in the cold store)

## Context

The shared library builds every name derived from a tenant (ADR-0003 dec 7), but whether a given name
or object-store key belongs to a tenant is decided separately by each mediator and read path. That test
guards the tenant boundary in every plane where tenants share a store. The copies have already
diverged: the cold read side accepts key forms the tracking side refuses, so the same key is inside
the tenant on one plane and refused on the other. A prefix test is only a boundary if the key means
what it spells; a store, proxy or client that normalises a key can move it under another prefix after
the test.

## Decision

1. **One shared implementation decides whether a name or an object-store key belongs to a tenant.** It
   lives in the tenant-resolution library beside the names it tests; every mediator and read path uses
   it, and the library's check refuses a service that implements it again.
2. **A key from a caller is tested only once it is plain: no empty, `.` or `..` segment and no
   backslash.** Such a key is refused, never normalised; normalising would be a second interpretation
   that the store, a proxy or a client can disagree with.
3. **A key still in its encoded form is refused if it carries an escape.** Decoded, it could spell a
   segment the plain test never saw.
4. **A caller's path is placed under its tenant's prefix by the same implementation, and only a plain
   path is placed.** Placing and testing are one rule, so a path the implementation places is one it
   would also accept.
5. **The rule holds for every object store a tenant reaches through a mediator, whatever the plane.**
   Keys a trusted writer assembles from typed parts are not caller input and are not tested.

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| 1 | Each mediator keeps its own test, held equal by review and tests | The copies diverged; each copy's own tests still passed |
| 1 | The test written into each plane's own decision record | Several homes for one rule (ADR-0000 dec 3); planes drift apart by construction |
| 2 | Refuse only `..` segments | `.` and empty segments and backslashes are rewritten by common stores, proxies and clients |
| 2 | Normalise the key, then test the prefix | Trusts that every later reader normalises the same way |
| 3 | Refuse `%` in every key, decoded ones included | Refuses legitimate artifact names; only an encoded key can hide a segment |
| 3 | Decode, then test | Another decode further on could still change the key |
| 5 | A weaker rule for read-only planes | A read across the boundary is the leak the boundary exists to prevent |

## Consequences

**Positive**
- One rule to review for every shared store; a new mediator inherits it rather than restating it.
- A key cannot be inside the tenant on one plane and refused on another.

**Negative**
- A change to the rule ships to every image that carries the library.
- Object names with empty, `.` or `..` segments or a backslash cannot be stored through a mediator.

## Deferred

| Open question | Settled by |
|---|---|
| Keys in a store that folds case or normalises Unicode | First supported object store that does |

## References

- `libs/industryflow_contracts/industryflow_contracts/tenancy.py`: the shared implementation.
- `tests/config_guards/test_tenant_contract_copies.py`: decision 1's check.
- `services/notebook_tracking_gateway/`, `services/notebook_cold_broker/`, `services/ml_service/api/`:
  the mediators and read paths that use it.
