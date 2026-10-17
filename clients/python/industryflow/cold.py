# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tenant-scoped access to the cold layer (long-horizon history) from an authoring notebook, through
the cold-store broker (ADR-0025 dec 5, read side).

The kernel holds NO durable object-store credential — only its per-session **cold capability
handle** (ADR-0012 dec 2, ADR-0015 dec 7). It presents that handle to the broker, which resolves
it to the caller's tenant and returns short-TTL, read-only, tenant-prefix-scoped pre-signed GET
URLs. Listing goes through the broker; the bytes of each Parquet file come **directly** from the
object store (the broker 307-redirects to the pre-signed URL), so many files can be read in
parallel, column- and partition-pruned — the whole point of the cold layer (ADR-0025 dec 1).

The HTTP transport is injectable, so the ergonomics are testable without a running broker or object
store. pandas/pyarrow are imported lazily (heavy); pyarrow is the ``cold`` extra.
"""
from __future__ import annotations

import json
import os
from typing import Any

# The env the notebook hub injects into an authoring kernel at spawn (jupyterhub_config.py).
BROKER_URL_ENV = "INDUSTRYFLOW_COLD_BROKER_URL"
CAPABILITY_ENV = "INDUSTRYFLOW_COLD_CAPABILITY"

# The exporter's layout (services/cold_export/naming.py): each day directory holds a manifest that
# lists the day's verified Parquet parts.
MANIFEST_NAME = "_manifest.json"
FIRST_PART = "measurements.parquet"


class IndustryFlowCold:
    """Read the caller's tenant cold-layer Parquet as DataFrames, via the broker.

    Args:
        broker_url: the cold-store broker origin, e.g. ``http://notebook-cold-broker:5060``.
        capability: the per-session cold capability handle (ADR-0025), sent as a bearer token.
        session: optional ``requests.Session``-like object (injectable for tests).
        verify: TLS verification, forwarded to the transport.
    """

    def __init__(self, broker_url: str, capability: str, *, session: Any = None, verify: bool = True):
        if not broker_url:
            raise ValueError("a cold-store broker URL is required")
        if not capability:
            raise ValueError("a cold capability handle is required (ADR-0025)")
        if session is None:
            import requests  # lazy so tests can inject a fake transport

            session = requests.Session()
        self._base = broker_url.rstrip("/")
        self._session = session
        self._verify = verify
        # The capability is the bearer the broker resolves to this tenant (ADR-0025 dec 5). On a
        # cross-host redirect to the pre-signed URL, requests drops the Authorization header, so the
        # handle is never sent to the object store — only the pre-signed signature is.
        self._session.headers["Authorization"] = f"Bearer {capability}"

    @classmethod
    def from_env(cls, *, session: Any = None, verify: bool = True) -> "IndustryFlowCold":
        """Build from the env the hub injects at spawn. Available only in an authoring kernel."""
        try:
            broker_url = os.environ[BROKER_URL_ENV]
            capability = os.environ[CAPABILITY_ENV]
        except KeyError as missing:
            raise RuntimeError(
                f"{missing.args[0]} is not set — the cold layer is available only in an authoring "
                "notebook spawned by the hub (ADR-0025)."
            ) from None
        return cls(broker_url, capability, session=session, verify=verify)

    def list_files(self, path: str = "") -> list:
        """List the tenant's Parquet objects (recursively). Paths are relative to the tenant's
        prefix — the kernel never sees the ``tenant_<uuid>/`` prefix. Returns ``[{"path","size"}]``."""
        resp = self._session.get(
            f"{self._base}/cold/files", params={"path": path}, verify=self._verify, timeout=30
        )
        resp.raise_for_status()
        return resp.json().get("files", [])

    def open(self, path: str) -> bytes:
        """Fetch one object's bytes. The broker 307-redirects to a pre-signed GET URL and the
        transport follows it, pulling the bytes straight from the object store."""
        resp = self._session.get(
            f"{self._base}/cold/object/{path.lstrip('/')}", verify=self._verify, timeout=300
        )
        resp.raise_for_status()
        return resp.content

    def read_parquet(self, path: str):
        """Read one Parquet object into a pandas DataFrame."""
        import io

        import pandas as pd  # lazy; heavy

        return pd.read_parquet(io.BytesIO(self.open(path)))

    def read_all(self, path: str = "", *, suffix: str = ".parquet"):
        """Read every verified Parquet part under ``path`` into one concatenated DataFrame.

        This is the "give me years, all columns" read: it lists the tenant's tree and reads each
        day's parts directly from the object store. A day's ``_manifest.json`` names the verified
        parts that make up the day — the first export plus any rows that arrived later — and exactly
        those are read. Parquet in a day with no manifest is an export still being written and
        verified, and is not read. For large histories, prefer narrowing ``path`` (e.g.
        ``"year=2026/month=07"``) to prune partitions.
        """
        import pandas as pd  # lazy; heavy

        files = [self._join(path, p) for p in self.verified_parts(path) if p.endswith(suffix)]
        if not files:
            return pd.DataFrame()
        return pd.concat([self.read_parquet(f) for f in files], ignore_index=True)

    def verified_parts(self, path: str = "") -> list:
        """The verified Parquet parts under ``path``, relative to it, in partition order."""
        listed = [f["path"] for f in self.list_files(path)]
        by_dir: dict = {}
        for rel in listed:
            directory, _, name = rel.rpartition("/")
            by_dir.setdefault(directory, set()).add(name)
        parts = []
        for directory in sorted(by_dir):
            if MANIFEST_NAME not in by_dir[directory]:
                continue
            manifest = json.loads(self.open(self._join(path, _rel(directory, MANIFEST_NAME))))
            for name in manifest_part_files(manifest):
                if name not in by_dir[directory]:
                    raise RuntimeError(
                        f"cold manifest {_rel(directory, MANIFEST_NAME)!r} lists {name!r}, "
                        "which is not in the store"
                    )
                parts.append(_rel(directory, name))
        return parts

    @staticmethod
    def _join(base: str, rel: str) -> str:
        base = base.strip("/")
        return f"{base}/{rel}" if base else rel


def _rel(directory: str, name: str) -> str:
    return f"{directory}/{name}" if directory else name


def manifest_part_files(manifest: dict) -> list:
    """The Parquet files a day's manifest lists. A manifest written before a day could hold more
    than one part lists none: it stands for the day's single first file."""
    if "parts" in manifest:
        return [str(p["file"]) for p in manifest["parts"]]
    return [FIRST_PART]


def read_all(path: str = ""):
    """Convenience: read the caller's cold history using the hub-injected cold capability."""
    return IndustryFlowCold.from_env().read_all(path)
