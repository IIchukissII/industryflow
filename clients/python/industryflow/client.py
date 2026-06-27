# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Thin, tenant-scoped client for loading a tenant's data into pandas DataFrames from inside a
notebook (ADR-0011 dec 4 — the default data path).

Phase-1 skeleton. It calls the existing api_gateway read endpoints over HTTP, authenticated as
the user. The credential is a per-session capability minted by the notebook spawner
(ADR-0012); this client just carries it as a bearer token — it never holds a database or
object-store credential. The HTTP transport is injectable so the ergonomics can be tested
without a running server.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional


def _iso(value: Optional[Any]) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _clean(params: dict) -> dict:
    return {k: v for k, v in params.items() if v is not None}


class IndustryFlowClient:
    """Load the caller's tenant data as DataFrames.

    Args:
        base_url: gateway origin, e.g. ``https://api.industryflow.local``.
        token: the per-session capability (ADR-0012) sent as ``Authorization: Bearer``.
        session: optional ``requests.Session``-like object (injectable for tests).
        verify: TLS verification, forwarded to the transport.
    """

    def __init__(self, base_url: str, token: Optional[str] = None, *, session: Any = None, verify: bool = True):
        if session is None:
            import requests  # imported lazily so tests can inject a fake transport

            session = requests.Session()
        self._base_url = base_url.rstrip("/")
        self._session = session
        self._verify = verify
        if token:
            self._session.headers["Authorization"] = f"Bearer {token}"

    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        resp = self._session.get(
            f"{self._base_url}{path}", params=_clean(params or {}), verify=self._verify, timeout=30
        )
        resp.raise_for_status()
        return resp.json()

    def _frame(self, records: list, time_col: str = "time"):
        import pandas as pd  # imported lazily; pandas is the point but heavy to import

        df = pd.DataFrame(records)
        if not df.empty and time_col in df.columns:
            df[time_col] = pd.to_datetime(df[time_col])
        return df

    def measurements(
        self,
        *,
        sensor_id: Optional[str] = None,
        equipment_id: Optional[str] = None,
        start: Optional[Any] = None,
        end: Optional[Any] = None,
        order: str = "asc",
        limit: int = 1000,
    ):
        """Raw measurements for the caller's tenant, optionally within ``[start, end]``."""
        params = {
            "sensor_id": sensor_id,
            "equipment_id": equipment_id,
            "start": _iso(start),
            "end": _iso(end),
            "order": order,
            "limit": limit,
        }
        return self._frame(self._get("/api/measurements", params))

    def aggregations(
        self,
        window: str,
        *,
        sensor_id: Optional[str] = None,
        equipment_id: Optional[str] = None,
        start: Optional[Any] = None,
        end: Optional[Any] = None,
        order: str = "asc",
        limit: int = 1000,
    ):
        """Windowed aggregations (``1min`` | ``5min`` | ``1hour``) for the caller's tenant."""
        params = {
            "sensor_id": sensor_id,
            "equipment_id": equipment_id,
            "start": _iso(start),
            "end": _iso(end),
            "order": order,
            "limit": limit,
        }
        return self._frame(self._get(f"/api/aggregations/{window}", params))

    def training_data(
        self,
        equipment_id: str,
        *,
        lookback_days: int = 30,
        min_quality: float = 0.8,
        limit: int = 10000,
    ):
        """A per-equipment training dataset for the caller's tenant (bulk historical pull)."""
        params = {"lookback_days": lookback_days, "min_quality": min_quality, "limit": limit}
        payload = self._get(f"/api/training-data/equipment/{equipment_id}", params)
        # This endpoint wraps its rows under "data"; the others return a bare list.
        records = payload.get("data", payload) if isinstance(payload, dict) else payload
        return self._frame(records)
