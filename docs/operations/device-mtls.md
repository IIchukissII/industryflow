<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Device ingestion over mTLS

Device authentication to ingestion with client certificates from the Device CA, and operation of the
CA, enrollment, revocation and CRL refresh. The browser-facing server certificate is a separate PKI
([TLS](tls.md)).

| Decision | ADR |
|---|---|
| mTLS, device identity | [ADR-0002](../../ADR/ADR-0002-ingestion-authentication-and-device-identity.md) |
| Device CA, CRL | [ADR-0007](../../ADR/ADR-0007-public-key-infrastructure.md) |

## Request path

```
device ──mTLS (client cert)──▶ ingestion edge ──verified cert──▶ ingestion-service ──▶ Kafka
                               verifies vs device CA + CRL       reads tenant from the cert SAN
```

| Step | Behaviour |
|---|---|
| Handshake | The edge verifies the client certificate against the device-CA chain and CRL (`ssl_verify_client on`, depth 2). Absent, untrusted or revoked: rejected (HTTP 400) |
| Forward | The edge sets the identity headers itself, overwriting any the client sent |
| Reachability | `ingestion-service` has no host port and sits only on networks shared with the edge and Kafka |
| Identity | The service reads `company_id` and `device_id` from the URI SAN `industryflow:tenant/<company-uuid>/device/<device-id>`, UUID-validates the tenant and routes the write by it. The body carries no tenant |
| Scope | Ingestion accepts only a verified client certificate, holds no database role, and produces only to Kafka |

`DEVICE_IDENTITY_HEADERS` names the header convention. The service reads only its own and refuses a
request carrying the other one's headers.

| Deployment | Edge | `DEVICE_IDENTITY_HEADERS` | Headers |
|---|---|---|---|
| Compose | `ingestion-edge` (nginx, host port `INGESTION_MTLS_PORT`) | `x-client` | `X-Client-Verify`, `X-Client-Cert` |
| Kubernetes | ingress-nginx, second Ingress (`ingress.mtls.enabled`) | `ssl-client` | `ssl-client-verify`, `ssl-client-cert` |

## CA commands

All state lives in git-ignored `deploy/device-ca/` (override: `DEVICE_CA_DIR`).

| Task | Command |
|---|---|
| Create root, issuing intermediate and CRLs | `scripts/device-ca.sh init` |
| Sign a device CSR | `scripts/device-ca.sh issue <company-uuid> <device-id> device.csr` |
| Issue a test key and certificate (local/test producers only) | `scripts/device-ca.sh issue <company-uuid> <device-id>` |
| Revoke a device and refresh the CRL | `scripts/device-ca.sh revoke <company-uuid> <device-id>` |
| Revoke every device of a deleted tenant and refresh the CRL | `scripts/device-ca.sh revoke-tenant <company-uuid>` |
| Re-sign the intermediate's CRL and the issued register | `scripts/device-ca.sh crl` |
| Re-sign the root's CRL (needs the offline root key) | `scripts/device-ca.sh root-crl` |
| Check both CRLs; non-zero inside the warning window | `scripts/device-ca.sh check` |
| Print the verify-chain path | `scripts/device-ca.sh chain` |

| File | Content |
|---|---|
| `root/root.key` | Root key; keep offline |
| `intermediate/` | Issuing intermediate (day-to-day signer) |
| `ca-chain.crt` | Intermediate + root; the edge's verify chain |
| `crl/device-ca.crl` | Root + intermediate CRLs; the edge's CRL |
| `crl/issued.tsv` | Issued register: the intermediate CRL's number, then `<serial> <notAfter> <tenant> <device>` per issued certificate; no keys |
| `crl/issued.tsv.sig` | SHA-256 signature of `issued.tsv` with the issuing key |
| `devices/<company-uuid>/<device-id>/<serial>.{crt,key,chain.crt}` | One set per issued certificate; `.key` only for test issuance |
| `devices/<company-uuid>/<device-id>/current.{crt,key,chain.crt}` | The newest serial |

| Name | Default | Meaning |
|---|---|---|
| `DEVICE_DAYS` | `90` | Device certificate validity |
| `INT_DAYS` / `ROOT_DAYS` | `1825` / `3650` | Intermediate / root validity |
| `CRL_DAYS` / `CRL_WARN_DAYS` | `7` / `3` | Intermediate CRL validity / warning window |
| `ROOT_CRL_DAYS` / `ROOT_CRL_WARN_DAYS` | `365` / `30` | Root CRL validity / warning window |

## Enrollment

| Rule | Detail |
|---|---|
| Tenant binding | Fixed at issuance by the operator; the device cannot choose its tenant |
| Real devices | Key generated on the device or secure element; only the CSR reaches the CA |
| Renewal | Issuing again adds a new serial and moves `current.*`; nothing is overwritten |
| Same `device_id`, two tenants | Two separate devices |

Reference producer against the edge: [getting started, step 5](../getting-started.md#5-stream-test-data).

## Revocation

`revoke` revokes every still-valid certificate of that tenant's device, renewals included, matched by
the SAN identity; another tenant's device with the same `device_id` is untouched. `revoke-tenant`
revokes every still-valid certificate of every device of the tenant; with none left it succeeds and
revokes nothing.

`issue`, `revoke`, `revoke-tenant` and `crl` each re-sign the intermediate's CRL and rewrite the
issued register beside it.

### Deleted tenants

The `tenant-delete` job records `device_certificates` only when, from the chain, the CRL bundle and
the issued register (no key), all of these hold
([ADR-0033](../../ADR/ADR-0033-tenant-lifecycle.md) dec 4):

| Check | Fails as |
|---|---|
| The chain is one intermediate signed by its root; the intermediate's CRL and the register verify against it | Job error |
| The intermediate's CRL has not expired | Job error |
| The register names the published CRL's number | Waiting |
| That CRL was signed after the deletion was requested | Waiting |
| Every unexpired certificate of the tenant in the register is on that CRL | Waiting; the log names the serials |

An expired certificate needs no revocation. A tenant with no certificate is recorded at once.

| Deployment | Operator step |
|---|---|
| Compose | `scripts/device-ca.sh revoke-tenant <company-uuid>`, then run `tenant-delete` again. The job mounts `ca-chain.crt` and `crl/` read-only |
| Kubernetes | `revoke-tenant` on the CA host, then publish the Secret with the register (below). The job mounts the Secret at `/etc/industryflow/device-ca` when `ingress.mtls.enabled` |

| Deployment | Takes effect |
|---|---|
| Compose | The edge polls the CRL file (`CRL_RELOAD_INTERVAL_SECONDS`, default `60`) and reloads nginx on change |
| Kubernetes | After the new CRL is published to the device-CA Secret (below) |

## CRL refresh

An expired CRL makes the edge refuse every device. Device certificates carry no CRL distribution
point; the edge holds the CRL locally ([ADR-0007](../../ADR/ADR-0007-public-key-infrastructure.md) dec 8).

| CRL | Lists | Re-signed by | Where |
|---|---|---|---|
| Intermediate | Revoked devices | `device-ca.sh crl` | Compose: `device-crl-refresh` service. Kubernetes: the CA host, at least every few days |
| Root | Revoked intermediates | `device-ca.sh root-crl` | Yearly root ceremony: bring the root key in, run it, take the key offline |

### Compose

`device-crl-refresh` runs `device-ca.sh crl` every `DEVICE_CRL_REFRESH_SECONDS` (default `86400`),
with no network, as `DEVICE_CA_UID`:`DEVICE_CA_GID` (default `1000:1000`, the CA directory's owner).
Its healthcheck runs `device-ca.sh check` hourly; `docker compose ps` shows it **unhealthy** when
either CRL is inside its warning window.

### Kubernetes

The issuing key is not in the cluster. Re-sign on the CA host and publish; ingress-nginx picks up
Secret changes:

```sh
scripts/device-ca.sh crl
kubectl -n <namespace> create secret generic industryflow-device-ca \
  --from-file=ca.crt=deploy/device-ca/ca-chain.crt \
  --from-file=ca.crl=deploy/device-ca/crl/device-ca.crl \
  --from-file=issued.tsv=deploy/device-ca/crl/issued.tsv \
  --from-file=issued.tsv.sig=deploy/device-ca/crl/issued.tsv.sig \
  --dry-run=client -o yaml | kubectl apply -f -
```

| Name | Default | Meaning |
|---|---|---|
| `ingress.mtls.caSecret` | `industryflow-device-ca` | Secret with `ca.crt`, `ca.crl`, `issued.tsv` and `issued.tsv.sig`; without `ca.crl` revocation is not checked and the CRL check fails; without the register no tenant deletion completes |
| `ingress.mtls.crlCheck.schedule` | `17 */6 * * *` | `device-crl-check` CronJob checking the Secret's CRL |
| `ingress.mtls.crlCheck.warnDays` / `rootWarnDays` | `3` / `30` | Warning windows |
| `ingress.mtls.crlCheck.alertAfterSeconds` | `46800` | No successful check for this long fires `IndustryFlowDeviceCrlStale` (needs `observability.prometheusRules.enabled` and kube-state-metrics) |

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Every device refused | A CRL expired | `device-ca.sh crl` (and `root-crl` if the root CRL expired); on Kubernetes republish the Secret |
| `device-ca.sh check` warns on the root CRL far ahead of the yearly ceremony | Root CRL signed with a shorter validity than `ROOT_CRL_DAYS` | `scripts/device-ca.sh root-crl`, then `docker compose up -d device-crl-refresh ingestion-edge` |
| `device-crl-refresh` exits: device CA not initialized | No `deploy/device-ca/intermediate` | `scripts/device-ca.sh init` |
| A deletion waits on `device_certificates` | The tenant's devices are not revoked, or the CRL and register were not republished since the request | `scripts/device-ca.sh revoke-tenant <company-uuid>`; on Kubernetes republish the Secret |
| `tenant-delete` fails: `No such file` for `issued.tsv` | CA files from before the register | `scripts/device-ca.sh crl`; on Kubernetes republish the Secret |
