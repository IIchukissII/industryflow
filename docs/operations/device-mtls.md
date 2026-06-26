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
docker compose exec ingestion-edge nginx -s reload   # reload so the edge picks up the CRL
```

A revoked device is refused at the edge immediately, without waiting for its certificate to
expire.

## Transition note

The JWT ingestion path is retained only as a labelled transition (ADR-0002 dec 8) and
should be removed once all producers present certificates. The previously committed mock
JWT has been removed with no bearer successor.
