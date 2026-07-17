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

It also serves a second, narrower population: a **person uploading an externally-authored artifact**
(ADR-0030). That plane exists here because this component holds the only credential that may write
the artifact store — not because admission is decided here; what may be *served* is the serving
side's judgement, at the gates it already operates (ADR-0030 dec 2 rev 1).

The two planes are distinguished only by their capability's **audience**, and that is load-bearing
rather than tidy: an uploaded artifact is refused if loading it would deserialise author-supplied
objects, and a kernel logging a model is not (ADR-0030 dec 4 resolves ADR-0027's deferral for the
upload path alone). Collapse the audiences and there is no third outcome — kernels inherit a refusal
nothing decided for them, or uploads escape the one that is their precondition.

An uploaded artifact is **staged, then judged, then admitted**, because those two decisions pull
against each other: the refusal needs the bytes, and dec 6 keeps this component out of the artifact
data path so it scales on request rate rather than artifact size. It reads each member's opening
bytes out of staging and promotes by a store-side copy, so it still never carries an artifact.

The auth dependency and the proxy/scoping orchestration are testable with a fake upstream + store
(no MLflow, no object store). The live adapters — httpx to MLflow, boto3 pre-signing — are the
cluster-bound part (``build_app``/``main``), validated against a real stack (issue #19-style).
"""
from __future__ import annotations

import os
from typing import Any, Awaitable, Callable, Optional, Protocol, Tuple

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

import admission
import policy
import scoping
import staging

_MLFLOW_PREFIX = "/api/2.0/mlflow/"
# The upload plane's own surface (ADR-0030). Deliberately not under MLflow's namespace: it is not
# MLflow's protocol, and a caller reaching it is not a kernel.
_UPLOAD_PREFIX = "/api/2.0/industryflow-upload"
# Enough of each member to frame what it is; the gateway reads heads, never artifacts.
_HEAD_BYTES = 16
# A manifest is a small declaration. A larger one is not a manifest this rule can read, and reading
# unbounded caller-supplied bytes into the mediator is the shape of the problem, not a solution.
_MANIFEST_MAX_BYTES = 256 * 1024
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
    address another's artifacts.

    ``head``/``copy``/``list_keys`` serve the upload plane (ADR-0030): an uploaded artifact is judged
    on its bytes, so the gateway must read *some* of them — but only each member's opening bytes,
    server-side, out of the staging area. It never carries the artifact itself, which is the property
    dec 6 exists to keep and the reason promotion is a store-side copy rather than a re-upload.
    """

    def presign(self, method: str, key: str) -> str: ...
    def list_files(self, prefix: str) -> list: ...      # [{"path","is_dir","file_size"}], paths relative to prefix
    def delete(self, key: str) -> None: ...
    def head(self, key: str, length: int) -> Optional[bytes]: ...   # first `length` bytes, or None if absent
    def copy(self, src: str, dst: str) -> None: ...                 # server-side; the bytes never transit the gateway
    def list_keys(self, prefix: str) -> list: ...                   # absolute keys under prefix


StoreGet = Callable[[str], Awaitable[Optional[object]]]

# Endpoints whose body references an experiment by id; the gateway must confirm the experiment is
# the caller's before proxying (the body carries no name to scope).
_EXPERIMENT_ID_ENDPOINTS = {"runs/create", "runs/search", "experiments/get", "experiments/delete"}
# Endpoints whose body references a run by id.
_RUN_ID_ENDPOINTS = {
    "runs/get", "runs/update", "runs/delete", "runs/log-metric", "runs/log-parameter",
    "runs/set-tag", "runs/delete-tag", "runs/log-batch",
}


def build_app(store_get: StoreGet, upstream: Upstream, artifacts: ArtifactStore,
              bucket: str = "") -> FastAPI:
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

    # --- the upload plane: staged, judged on structure, then admitted (ADR-0030) ---

    async def uploader(request: Request) -> policy.TrackingBinding:
        """Resolve the bearer as an *upload* handle, or 401 (ADR-0030 dec 3).

        A tracking handle never resolves here. That is not defence in depth — it is the only thing
        holding two populations to different rules at one mediator: an uploaded artifact faces the
        structural refusal below, a kernel logging a model does not.
        """
        auth = request.headers.get("authorization", "")
        handle = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
        binding = await policy.resolve_upload_binding(store_get, handle)
        if binding is None:
            raise HTTPException(status_code=401, detail="upload capability refused")
        return binding

    @app.post(_UPLOAD_PREFIX + "/stage")
    async def upload_stage(request: Request, binding: policy.TrackingBinding = Depends(uploader)):
        """Begin an upload: return one pre-signed PUT per member, into the staging area.

        The caller names its members; the gateway decides every key. Bytes go straight to the store
        (ADR-0019 dec 6) — into a place no tenant can address, because nothing has judged them yet.
        """
        body = await _json_body(request) or {}
        members = body.get("files")
        if not isinstance(members, list) or not members:
            raise HTTPException(status_code=400, detail="an upload must name its files")
        if len(members) > staging.MAX_MEMBERS:
            raise HTTPException(status_code=400, detail=f"an artifact may carry at most {staging.MAX_MEMBERS} files")

        safe = {}
        for raw in members:
            member = staging.safe_member_path(raw if isinstance(raw, str) else "")
            if member is None:
                raise HTTPException(status_code=400, detail=f"unusable file path: {raw!r}")
            safe[member] = raw
        if admission.MANIFEST_NAME not in safe:
            # Without it there is nothing to judge, and judging is the point of the plane.
            raise HTTPException(status_code=400, detail=f"an artifact must carry its {admission.MANIFEST_NAME} manifest")

        token = policy.tenant_token(binding.company_id)
        upload_id = staging.new_upload_id()
        urls = {
            member: artifacts.presign("PUT", staging.staging_key(token, upload_id, member))
            for member in safe
        }
        return JSONResponse({"upload_id": upload_id, "urls": urls})

    @app.post(_UPLOAD_PREFIX + "/commit")
    async def upload_commit(request: Request, binding: policy.TrackingBinding = Depends(uploader)):
        """Judge a staged artifact and either admit it into the tenant's prefix, or refuse and
        discard it (ADR-0030 dec 4).

        The tenant is the handle's, never the request's: the staging prefix is derived from the
        binding, so a caller cannot commit an upload it did not stage — including another tenant's.
        """
        body = await _json_body(request) or {}
        upload_id = body.get("upload_id")
        if not isinstance(upload_id, str) or not staging.is_upload_id(upload_id):
            raise HTTPException(status_code=400, detail="unknown upload")

        token = policy.tenant_token(binding.company_id)
        prefix = staging.staging_prefix(token, upload_id)
        keys = artifacts.list_keys(prefix)
        if not keys:
            raise HTTPException(status_code=404, detail="nothing staged for this upload")

        manifest_key = prefix + admission.MANIFEST_NAME
        manifest_bytes = artifacts.head(manifest_key, _MANIFEST_MAX_BYTES)
        manifest_text = manifest_bytes.decode("utf-8", "replace") if manifest_bytes else None

        heads = [(key[len(prefix):], artifacts.head(key, _HEAD_BYTES) or b"") for key in keys]
        verdict = admission.evaluate(manifest_text, heads)

        if verdict.refused:
            # Discard rather than leave it lying about: refused bytes have no reason to persist, and
            # the staging area is not a quarantine to be curated.
            for key in keys:
                artifacts.delete(key)
            raise HTTPException(status_code=422, detail=verdict.reason)

        artifact_prefix = policy.artifact_prefix(binding.company_id)
        for key in keys:
            member = key[len(prefix):]
            artifacts.copy(key, staging.admitted_key(artifact_prefix, upload_id, member))
        for key in keys:
            artifacts.delete(key)

        return JSONResponse({
            "upload_id": upload_id,
            "artifact_uri": staging.admitted_uri(bucket, artifact_prefix, upload_id),
            "files": sorted(m for m, _ in heads),
        })

    # --- tracking/registry metadata: tenant-namespaced, proxied to MLflow ---

    @app.api_route(_MLFLOW_PREFIX + "{endpoint:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
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
    from botocore.exceptions import ClientError

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
    # ADR-0030 dec 10 (rev 2): a pre-signed PUT is followed by the CLIENT, and that client may be a
    # browser outside the trust network. The URL must therefore name the address the client can reach
    # — the public edge — not the interior one the gateway reaches the store on. This second client is
    # identical to the one above but for its endpoint; it signs URLs only. The gateway's own
    # server-side ops (list/head/copy/delete) keep the interior client. Unset → the same endpoint as
    # before, so an in-cluster client (the kernel) is unaffected, and the two are the same object.
    presign_endpoint = os.environ.get("TRACKING_S3_PRESIGN_ENDPOINT_URL")
    if presign_endpoint:
        s3_presign = boto3.client(
            "s3",
            endpoint_url=presign_endpoint,
            aws_access_key_id=os.environ["TRACKING_S3_ACCESS_KEY"],
            aws_secret_access_key=os.environ["TRACKING_S3_SECRET_KEY"],
            region_name=os.environ.get("TRACKING_S3_REGION", "us-east-1"),
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
    else:
        s3_presign = s3
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
            # Signed against the endpoint the CLIENT can reach (TRACKING_S3_PRESIGN_ENDPOINT_URL, the
            # public edge for a browser; the interior endpoint for an in-cluster kernel). SigV4 signs
            # host+key only, so the client's follow-the-signature PUT lands at that host, and the store
            # behind the edge recomputes the same signature over the host it is handed (ADR-0030 dec 10).
            return s3_presign.generate_presigned_url(op, Params={"Bucket": bucket, "Key": key}, ExpiresIn=presign_ttl)

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

        def head(self, key, length):
            # A ranged read: the gateway needs each member's opening bytes to frame what it is, and
            # nothing more. Reading whole artifacts here would undo ADR-0019 dec 6.
            try:
                resp = s3.get_object(Bucket=bucket, Key=key, Range=f"bytes=0-{max(length - 1, 0)}")
            except s3.exceptions.NoSuchKey:
                return None
            except ClientError as e:
                if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404", "InvalidRange"):
                    return None
                raise
            return resp["Body"].read(length)

        def copy(self, src, dst):
            # Server-side: promotion moves an admitted artifact without the bytes transiting here.
            s3.copy_object(Bucket=bucket, CopySource={"Bucket": bucket, "Key": src}, Key=dst)

        def list_keys(self, prefix):
            out, token = [], None
            while True:
                kw = {"Bucket": bucket, "Prefix": prefix}
                if token:
                    kw["ContinuationToken"] = token
                resp = s3.list_objects_v2(**kw)
                out.extend(obj["Key"] for obj in resp.get("Contents", []))
                if not resp.get("IsTruncated"):
                    break
                token = resp.get("NextContinuationToken")
            return out

    app = build_app(store.get, _HttpUpstream(), _S3Store(), bucket=bucket)
    uvicorn.run(app, host=os.environ.get("TRACKING_HOST", "0.0.0.0"), port=int(os.environ.get("TRACKING_PORT", "5050")))


if __name__ == "__main__":  # pragma: no cover
    main()
