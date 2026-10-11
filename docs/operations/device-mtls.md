<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Device ingestion over mTLS

Device/gateway producers authenticate to ingestion with **mutual TLS**, not a bearer token
(**[ADR-0002](../../ADR/ADR-0002-ingestion-authentication-and-device-identity.md)**). A
device presents a client certificate issued by IndustryFlow's **device CA**
(**[ADR-0007](../../ADR/ADR-0007-public-key-infrastructure.md)**); the terminating edge
verifies it and the ingestion service reads the tenant from the verified certificate —
never from the request body.

This is a distinct PKI from the browser-facing server certificate in
[tls.md](tls.md): one authenticates **devices to us**, the other authenticates **us to
browsers**.

## How a write is authenticated

```
device ──mTLS (client cert)──▶ ingestion edge (nginx) ──verified cert──▶ ingestion-service
                               verifies vs device CA + CRL                 reads tenant from
                                                                           the cert SAN
```

1. The device opens a TLS connection to the edge and presents its client certificate.
2. The edge verifies the certificate against the device-CA chain and the CRL
   (`ssl_verify_client on`). An absent, untrusted, or revoked certificate is rejected at
   the handshake (HTTP 400).
3. The edge forwards the request to `ingestion-service` (which has **no host port** — it is
   reachable only via the edge) with the verified certificate in `X-Client-Cert`. These
   headers are set by the proxy, so a client cannot spoof them.
4. Ingestion reads `company_id` + `device_id` from the certificate's URI SAN
   (`industryflow:tenant/<uuid>/device/<id>`), UUID-validates the tenant, and uses it to
   route the write. The body never carries a tenant.

## Run the device CA

```sh
scripts/device-ca.sh init                       # offline root + issuing intermediate + CRL
```

Output lives in git-ignored `deploy/device-ca/`. Keep `root/root.key` offline; the
intermediate is the day-to-day signer.

## Enroll a device

Enrollment binds a device to a tenant **at issuance** — the device cannot choose its own
tenant. For a real device, generate the key in its secure element, send the CSR, and sign it:

```sh
scripts/device-ca.sh issue <company_uuid> <device_id> device.csr
```

For a local/test producer (key generated on the CA host — never for real devices):

```sh
scripts/device-ca.sh issue <company_uuid> <device_id>
# -> deploy/device-ca/devices/<device_id>/{<id>.crt,<id>.key,<id>.chain.crt}
```

Point a producer at the edge with its certificate:

```sh
INGESTION_URL=https://<host>:8443/ingest \
DEVICE_CERT=deploy/device-ca/devices/<id>/<id>.chain.crt \
DEVICE_KEY=deploy/device-ca/devices/<id>/<id>.key \
  python3 extensions/tep-reference/producer/stream_tep_data.py
```

## Revoke a device

```sh
scripts/device-ca.sh revoke <device_id>         # refreshes the CRL
```

On Compose the edge reloads itself within a minute of the CRL changing. A revoked device is then
refused at the edge, without waiting for its certificate to expire. On Kubernetes, publish the
new CRL to the device-CA Secret as described under *Keep the CRL fresh*.

## Keep the CRL fresh

The edge checks every device against the CRL. **A CRL has its own expiry, and once it passes
the edge refuses every device**, revoked or not. Each CRL is therefore re-signed well inside its
validity ([ADR-0007](../../ADR/ADR-0007-public-key-infrastructure.md) dec 8):

| CRL | Lists | Re-signed by | Validity | Raised when less than |
|---|---|---|---|---|
| Intermediate | revoked devices | `device-ca.sh crl` (intermediate key only) | `CRL_DAYS` = 7 d | `CRL_WARN_DAYS` = 3 d |
| Root | revoked intermediates | `device-ca.sh root-crl` (needs the offline root key) | `ROOT_CRL_DAYS` = 365 d | `ROOT_CRL_WARN_DAYS` = 30 d |

`device-ca.sh check` reports both and exits non-zero when either is inside its warning window.
Device certificates carry no CRL distribution point. The edge holds the CRL locally.

**Compose.** The `device-crl-refresh` service re-signs the intermediate's CRL daily
(`DEVICE_CRL_REFRESH_SECONDS`). It has no network and runs as the CA directory's owner
(`DEVICE_CA_UID`/`DEVICE_CA_GID`). The edge reloads when the file changes. The service goes
**unhealthy** when either CRL nears expiry, which shows in `docker compose ps`.

**Root ceremony (both deployments), yearly.** Bring the root key in, run
`scripts/device-ca.sh root-crl`, then take the key offline again. The routine refresh never
needs it.

**Kubernetes.** The issuing key does not live in the cluster. Re-sign where the CA lives, at
least every few days, and publish the result. ingress-nginx picks up Secret changes on its own:

```sh
scripts/device-ca.sh crl
kubectl -n <ns> create secret generic industryflow-device-ca \
  --from-file=ca.crt=deploy/device-ca/ca-chain.crt \
  --from-file=ca.crl=deploy/device-ca/crl/device-ca.crl \
  --dry-run=client -o yaml | kubectl apply -f -
```

The chart's `device-crl-check` CronJob (`ingress.mtls.crlCheck`) checks the Secret's CRL every
6 hours. When it fails, the `IndustryFlowDeviceCrlStale` alert fires (requires
`observability.prometheusRules.enabled` and kube-state-metrics). The Secret must contain
`ca.crl`. Without it, revocation is not checked at all and the check fails.

**Upgrading an existing CA.** Older root CRLs were signed for 30 days. Run
`scripts/device-ca.sh root-crl` once to re-sign with the long validity, then
`docker compose up -d device-crl-refresh ingestion-edge`.

## mTLS only

Ingestion accepts **only** a verified client certificate. The transitional JWT ingestion
path (ADR-0002 dec 8) has been removed now that producers present certificates, and with it
the service's database access: ingestion holds no database role, never queries the database,
and produces only to Kafka. There is no bearer successor to the previously committed mock JWT.
