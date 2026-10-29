# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Cold-store broker — the wire entry point (ADR-0025 dec 5, read side).

A notebook reads long-horizon history from the cold layer through this broker. The kernel holds
NO durable object-store credential (ADR-0012 dec 2, ADR-0015 dec 7) — only its per-session *cold*
capability handle. The broker:

  1. authenticates the bearer, resolving it to one tenant (``policy.resolve_cold_outcome``), and
     records the outcome into the capability-use record (ADR-0042);
  2. confines every object-store key to that tenant's ``tenant_<uuid>/`` prefix
     (``policy.scope_key`` / ``policy.owns_key``) — a tenant can never address another's Parquet;
  3. lists the tenant's objects, and for each returns a **short-TTL, per-object, read-only
     pre-signed GET URL** so bytes move directly between the kernel and the object store — in
     parallel, column/partition-pruned by the reader — with the privileged principal never
     entering the kernel (ADR-0025 dec 5; the object-store twin of the SQL proxy).

This is the read-only cousin of the experiment-tracking gateway (ADR-0019): list + presigned GET,
no PUT and no DELETE. The broker holds a **read-only** cold-bucket principal — distinct from the
exporter's write-scoped principal and from the tracking gateway's artifact principal.

The auth + scoping orchestration is testable with a fake store + object store (no Redis, no S3);
the live adapters (aioredis, boto3 pre-signing) are the cluster-bound part (``main``).
"""
from __future__ import annotations

import os
from typing import Optional, Protocol

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from industryflow_contracts import tenancy

import policy


class ColdStore(Protocol):
    """The tenant-scoped, read-only object store the broker vends access to (ADR-0025 dec 5)."""

    def presign_get(self, key: str) -> str: ...
    def list_files(self, prefix: str) -> list: ...  # [{"path","size"}], path relative to prefix


class CapabilityStore(Protocol):
    """The capability store (redis.asyncio): the handle lookup, and the record's appends (ADR-0042)."""

    async def get(self, key: str) -> Optional[object]: ...
    async def eval(self, script: str, numkeys: int, *keys_and_args: object) -> object: ...
    async def xadd(self, name: str, fields: dict, **kwargs: object) -> object: ...


def build_app(store: CapabilityStore, cold: ColdStore) -> FastAPI:
    app = FastAPI(title="IndustryFlow notebook cold-store broker")

    async def tenant(request: Request) -> policy.ColdBinding:
        """Resolve the bearer cold capability to a tenant, or 401 (ADR-0025 dec 5).

        A presented handle's outcome is recorded before anything is served (ADR-0042 dec 1, 5); a
        record that cannot be written fails the request, as a store that cannot answer the lookup
        does. A request presenting no bearer presented no capability and is not recorded."""
        auth = request.headers.get("authorization", "")
        handle = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
        binding, reason, facts = await policy.resolve_cold_outcome(store.get, handle)
        if handle:
            await policy.record_resolution(store, handle, binding, reason, facts,
                                           source=request.client.host if request.client else None)
        if binding is None:
            raise HTTPException(status_code=401, detail="cold capability refused")
        return binding

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/cold/files")
    async def list_files(request: Request, binding: policy.ColdBinding = Depends(tenant)):
        """List the tenant's Parquet (recursively). Paths are RELATIVE to the tenant prefix, so a
        kernel sees only its own tree and never the tenant_<uuid>/ prefix."""
        try:
            prefix = policy.scope_key(binding.company_id, request.query_params.get("path", ""))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return JSONResponse({"files": cold.list_files(prefix)})

    @app.get("/cold/object/{path:path}")
    async def get_object(path: str, binding: policy.ColdBinding = Depends(tenant)):
        """Read one object: 307 to a short-TTL, read-only pre-signed GET URL. The kernel (or its
        pandas/pyarrow reader) follows the redirect and pulls bytes straight from the object store;
        the broker is never in the byte path and holds the only object-store credential."""
        try:
            key = policy.scope_key(binding.company_id, path)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if not policy.owns_key(binding.company_id, key):  # defence in depth (scope_key already forces it)
            raise HTTPException(status_code=403, detail="cross-tenant object")
        return RedirectResponse(cold.presign_get(key), status_code=307)

    return app


# --------------------------------------------------------------------- live adapters (cluster-bound)

def main() -> None:  # pragma: no cover - cluster-bound entry point
    import boto3
    import redis.asyncio as aioredis
    import uvicorn
    from botocore.client import Config

    store = aioredis.from_url(os.environ["COLD_BROKER_REDIS_URL"])
    bucket = os.environ["COLD_STORE_BUCKET"]
    presign_ttl = int(os.environ.get("COLD_BROKER_PRESIGN_TTL_SECONDS", "300"))
    s3 = boto3.client(
        "s3",
        endpoint_url=os.environ.get("COLD_STORE_ENDPOINT"),
        # READ-ONLY cold principal — distinct from the exporter's write-scoped principal and never
        # the root keys (ADR-0025 dec 11). The kernel never sees these.
        aws_access_key_id=os.environ["COLD_READ_ACCESS_KEY"],
        aws_secret_access_key=os.environ["COLD_READ_SECRET_KEY"],
        region_name=os.environ.get("COLD_STORE_REGION", "us-east-1"),
        # SigV4 + path-style: MinIO does not serve virtual-host buckets; SigV4 presigned GETs sign
        # host+key so the kernel's follow-the-redirect GET is accepted.
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )

    class _S3ColdStore:
        def presign_get(self, key):
            return s3.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": key},
                                             ExpiresIn=presign_ttl)

        def list_files(self, prefix):
            prefix = prefix.rstrip("/") + "/" if prefix else ""
            out, token = [], None
            while True:
                kw = {"Bucket": bucket, "Prefix": prefix}  # no Delimiter: recurse the whole tree
                if token:
                    kw["ContinuationToken"] = token
                resp = s3.list_objects_v2(**kw)
                for obj in resp.get("Contents", []):
                    rel = tenancy.owned_name(prefix, obj["Key"])
                    if rel:
                        out.append({"path": rel, "size": obj["Size"]})
                if not resp.get("IsTruncated"):
                    break
                token = resp.get("NextContinuationToken")
            return out

    app = build_app(store, _S3ColdStore())
    uvicorn.run(app, host=os.environ.get("COLD_BROKER_HOST", "0.0.0.0"),
                port=int(os.environ.get("COLD_BROKER_PORT", "5060")))


if __name__ == "__main__":  # pragma: no cover
    main()
