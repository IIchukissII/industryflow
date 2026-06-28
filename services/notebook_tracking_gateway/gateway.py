# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Experiment-tracking gateway — the wire entry point (ADR-0019).

A notebook's MLflow client points ``MLFLOW_TRACKING_URI`` at this gateway and sends its tracking
capability as the bearer token (``MLFLOW_TRACKING_TOKEN``). The gateway:

  1. authenticates the bearer, resolving it to one tenant (``policy.resolve_tracking_binding``);
  2. forces every MLflow REST call into that tenant's namespace — qualifying name fields on the
     way in, stripping them on the way out, and validating that any referenced experiment/run id
     belongs to the tenant before proxying (``scoping`` + ``_ensure_owns``);
  3. for artifacts, returns **tenant-scoped, per-object, short-TTL pre-signed URLs** so bytes move
     directly between the kernel and the object store — no object-store credential in the kernel
     (ADR-0019 dec 6).

The auth dependency and the proxy/scoping orchestration are testable with a fake upstream + store
(no MLflow, no object store). The live adapters — httpx to MLflow, boto3 pre-signing — are the
cluster-bound part (``build_app``/``main``), validated against a real stack (issue #19-style).
"""
from __future__ import annotations

import os
from typing import Any, Awaitable, Callable, Optional, Protocol, Tuple

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

import policy
import scoping

_MLFLOW_PREFIX = "/api/2.0/mlflow/"
# MLflow's proxied-artifact API. With the server's default-artifact-root set to `mlflow-artifacts:/`,
# the client uploads/downloads/lists artifacts here instead of touching the object store directly —
# so no object-store credential is ever in the kernel (ADR-0019 dec 1).
_ARTIFACT_PREFIX = "/api/2.0/mlflow-artifacts/artifacts"


class Upstream(Protocol):
    """A proxied call to the MLflow tracking server. Returns (status, json-or-None)."""

    async def call(self, method: str, endpoint: str, *, params: dict, body: Optional[dict]) -> Tuple[int, Any]: ...


class ArtifactStore(Protocol):
    """The tenant-scoped object store the gateway owns for artifacts (ADR-0019 dec 5-6). The gateway
    forces every key under the tenant prefix, so bytes go direct (pre-signed) and a tenant can never
    address another's artifacts."""

    def presign(self, method: str, key: str) -> str: ...
    def list_files(self, prefix: str) -> list: ...      # [{"path","is_dir","file_size"}], paths relative to prefix
    def delete(self, key: str) -> None: ...


StoreGet = Callable[[str], Awaitable[Optional[object]]]

# Endpoints whose body references an experiment by id; the gateway must confirm the experiment is
# the caller's before proxying (the body carries no name to scope).
_EXPERIMENT_ID_ENDPOINTS = {"runs/create", "runs/search", "experiments/get", "experiments/delete"}
# Endpoints whose body references a run by id.
_RUN_ID_ENDPOINTS = {
    "runs/get", "runs/update", "runs/delete", "runs/log-metric", "runs/log-parameter",
    "runs/set-tag", "runs/delete-tag", "runs/log-batch",
}


def build_app(store_get: StoreGet, upstream: Upstream, artifacts: ArtifactStore) -> FastAPI:
    app = FastAPI(title="IndustryFlow notebook tracking gateway")

    async def tenant(request: Request) -> policy.TrackingBinding:
        """Resolve the bearer capability to a tenant, or 401 (ADR-0019 dec 1, 4)."""
        auth = request.headers.get("authorization", "")
        handle = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
        binding = await policy.resolve_tracking_binding(store_get, handle)
        if binding is None:
            raise HTTPException(status_code=401, detail="tracking capability refused")
        return binding

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    # --- artifacts: bytes go direct kernel↔store via a tenant-scoped pre-signed URL (dec 6) ---

    @app.api_route(_ARTIFACT_PREFIX + "/{artifact_path:path}", methods=["GET", "PUT", "DELETE"])
    async def artifact_object(artifact_path: str, request: Request,
                              binding: policy.TrackingBinding = Depends(tenant)):
        key = policy.scope_artifact_key(binding.company_id, artifact_path)  # force under tenant prefix
        if request.method == "DELETE":
            artifacts.delete(key)
            return JSONResponse({})
        # PUT (upload) / GET (download): 307 to a single-object pre-signed URL — requests/MLflow
        # follows it, re-sending the body for PUT, so the gateway is never in the byte path.
        verb = "PUT" if request.method == "PUT" else "GET"
        return RedirectResponse(artifacts.presign(verb, key), status_code=307)

    @app.get(_ARTIFACT_PREFIX)
    async def artifact_list(request: Request, binding: policy.TrackingBinding = Depends(tenant)):
        prefix = policy.scope_artifact_key(binding.company_id, request.query_params.get("path", ""))
        return JSONResponse({"files": artifacts.list_files(prefix)})

    # --- tracking/registry metadata: tenant-namespaced, proxied to MLflow ---

    @app.api_route(_MLFLOW_PREFIX + "{endpoint:path}", methods=["GET", "POST", "DELETE"])
    async def proxy(endpoint: str, request: Request, binding: policy.TrackingBinding = Depends(tenant)):
        cid = binding.company_id
        params = dict(request.query_params)
        body = await _json_body(request)

        # Validate any referenced ids belong to the tenant (the body has no name to scope).
        await _ensure_owns(upstream, cid, endpoint, params, body)

        # Qualify request names → proxy → strip response names (and drop foreign entries).
        scoped_params = scoping.scope_request(endpoint, params, cid) if request.method == "GET" else params
        scoped_body = scoping.scope_request(endpoint, body, cid) if body is not None else None
        status, data = await upstream.call(request.method, endpoint, params=scoped_params, body=scoped_body)
        if isinstance(data, dict):
            data = scoping.scope_response(endpoint, data, cid)
        return JSONResponse(status_code=status, content=data)

    return app


async def _json_body(request: Request) -> Optional[dict]:
    if request.method == "GET":
        return None
    raw = await request.body()
    if not raw:
        return {}
    import json
    try:
        return json.loads(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid JSON body")


async def _ensure_owns(upstream: Upstream, company_id: str, endpoint: str, params: dict, body: Optional[dict]) -> None:
    """Refuse an id-bearing call whose experiment/run is not the caller's tenant's (ADR-0019 dec 5)."""
    src = {**(body or {}), **params}
    if endpoint in _EXPERIMENT_ID_ENDPOINTS:
        for exp_id in _as_list(src.get("experiment_id") or src.get("experiment_ids")):
            await _assert_experiment_owned(upstream, company_id, exp_id)
    if endpoint in _RUN_ID_ENDPOINTS and src.get("run_id"):
        status, data = await upstream.call("GET", "runs/get", params={"run_id": src["run_id"]}, body=None)
        exp_id = (((data or {}).get("run") or {}).get("info") or {}).get("experiment_id") if status == 200 else None
        await _assert_experiment_owned(upstream, company_id, exp_id)


async def _assert_experiment_owned(upstream: Upstream, company_id: str, experiment_id) -> None:
    if not experiment_id:
        raise HTTPException(status_code=403, detail="cross-tenant or unknown experiment")
    status, data = await upstream.call("GET", "experiments/get", params={"experiment_id": str(experiment_id)}, body=None)
    name = (((data or {}).get("experiment")) or {}).get("name") if status == 200 else None
    if not name or not policy.owns_name(company_id, name):
        raise HTTPException(status_code=403, detail="cross-tenant or unknown experiment")


def _as_list(v) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


# --------------------------------------------------------------------- live adapters (cluster-bound)

def main() -> None:  # pragma: no cover - cluster-bound entry point
    import boto3
    import httpx
    import redis.asyncio as aioredis
    import uvicorn
    from botocore.client import Config

    mlflow_url = os.environ["TRACKING_MLFLOW_URL"].rstrip("/")
    store = aioredis.from_url(os.environ["TRACKING_REDIS_URL"])
    bucket = os.environ["TRACKING_ARTIFACT_BUCKET"]
    presign_ttl = int(os.environ.get("TRACKING_PRESIGN_TTL_SECONDS", "300"))
    s3 = boto3.client(
        "s3",
        endpoint_url=os.environ.get("TRACKING_S3_ENDPOINT_URL"),
        aws_access_key_id=os.environ["TRACKING_S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["TRACKING_S3_SECRET_KEY"],
        region_name=os.environ.get("TRACKING_S3_REGION", "us-east-1"),
        # SigV4 + path-style: MinIO rejects SigV2 presigned PUTs that carry extra headers, and does
        # not serve virtual-host buckets. SigV4 presigned URLs sign only host+key, so the client's
        # follow-the-redirect PUT (with its own Content-Type) is accepted.
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    client = httpx.AsyncClient(base_url=mlflow_url, timeout=30)

    class _HttpUpstream:
        async def call(self, method, endpoint, *, params, body):
            r = await client.request(method, _MLFLOW_PREFIX + endpoint, params=params, json=body)
            try:
                return r.status_code, r.json()
            except ValueError:
                return r.status_code, None

    class _S3Store:
        def presign(self, method, key):
            op = "get_object" if method == "GET" else "put_object"
            # The endpoint MLflow's client reaches us on (TRACKING_PUBLIC_URL) is the host the
            # pre-signed URL must point at; default to the S3 endpoint for in-cluster use.
            return s3.generate_presigned_url(op, Params={"Bucket": bucket, "Key": key}, ExpiresIn=presign_ttl)

        def list_files(self, prefix):
            prefix = prefix.rstrip("/") + "/" if prefix else ""
            out, token = [], None
            while True:
                kw = {"Bucket": bucket, "Prefix": prefix, "Delimiter": "/"}
                if token:
                    kw["ContinuationToken"] = token
                resp = s3.list_objects_v2(**kw)
                for cp in resp.get("CommonPrefixes", []):
                    out.append({"path": cp["Prefix"][len(prefix):].rstrip("/"), "is_dir": True, "file_size": None})
                for obj in resp.get("Contents", []):
                    rel = obj["Key"][len(prefix):]
                    if rel:
                        out.append({"path": rel, "is_dir": False, "file_size": obj["Size"]})
                if not resp.get("IsTruncated"):
                    break
                token = resp.get("NextContinuationToken")
            return out

        def delete(self, key):
            s3.delete_object(Bucket=bucket, Key=key)

    app = build_app(store.get, _HttpUpstream(), _S3Store())
    uvicorn.run(app, host=os.environ.get("TRACKING_HOST", "0.0.0.0"), port=int(os.environ.get("TRACKING_PORT", "5050")))


if __name__ == "__main__":  # pragma: no cover
    main()
