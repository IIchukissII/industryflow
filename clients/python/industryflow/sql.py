# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tenant-scoped SQL access from an authoring notebook, through the SQL access proxy
(ADR-0015 dec 5-6).

The kernel connects to the **proxy** with a normal Postgres driver, presenting its per-session
**SQL capability handle** (ADR-0012/0015) as the password — never a database credential, and never
the database directly. The proxy resolves the handle to the caller's tenant, assumes that tenant's
read-only role, and relays (the ADR-0011 keystone: tenant isolation by DB privilege, enforced
server-side). This module is the ergonomic wrapper: it reads the endpoint + capability the hub
injects at spawn (authoring profile only) and returns a connection or a DataFrame. The connector
is injectable, so the credential wiring is testable without psycopg or a running proxy.
"""
from __future__ import annotations

import os
from typing import Any, Callable, Optional

# The env the notebook hub injects into an authoring kernel at spawn (jupyterhub_config.py).
PROXY_URL_ENV = "INDUSTRYFLOW_SQL_PROXY_URL"
CAPABILITY_ENV = "INDUSTRYFLOW_SQL_CAPABILITY"

# The startup username is ignored by the proxy — it logs in upstream as its own privileged role
# (ADR-0015) — so a fixed, descriptive value avoids depending on the kernel's OS user.
_PROXY_USER = "notebook"


class IndustryFlowSQL:
    """Tenant-scoped SQL access via the proxy.

    Args:
        dsn: the SQL proxy endpoint, e.g. ``postgresql://notebook-sql-proxy:6432/industryflow``.
        capability: the per-session SQL capability handle (ADR-0015), used as the password.
        connect: optional DBAPI ``connect`` callable (injectable for tests).
    """

    def __init__(self, dsn: str, capability: str, *, connect: Optional[Callable[..., Any]] = None):
        if not dsn:
            raise ValueError("a SQL proxy DSN is required")
        if not capability:
            raise ValueError("a SQL capability handle is required (ADR-0015)")
        self._dsn = dsn
        self._capability = capability
        self._connect = connect

    @classmethod
    def from_env(cls, *, connect: Optional[Callable[..., Any]] = None) -> "IndustryFlowSQL":
        """Build from the env the hub injects at spawn. Available only in an authoring kernel."""
        try:
            dsn = os.environ[PROXY_URL_ENV]
            capability = os.environ[CAPABILITY_ENV]
        except KeyError as missing:
            raise RuntimeError(
                f"{missing.args[0]} is not set — tenant SQL is available only in an authoring "
                "notebook spawned by the hub (ADR-0015)."
            ) from None
        return cls(dsn, capability, connect=connect)

    def connect(self):
        """Open a DBAPI connection to the proxy, authenticating with the capability handle."""
        connect = self._connect
        if connect is None:
            import psycopg  # lazy: psycopg is an optional extra (install ``industryflow[sql]``)

            connect = psycopg.connect
        # The capability handle is the password (ADR-0015); the proxy holds the real DB credential
        # and SET ROLEs into the tenant's read-only role. The kernel never sees a DB password.
        return connect(self._dsn, user=_PROXY_USER, password=self._capability)

    def query(self, sql: str, params: Optional[Any] = None):
        """Run a (read-only) query through the proxy and return a pandas DataFrame."""
        import pandas as pd  # lazy; heavy to import

        con = self.connect()
        try:
            return pd.read_sql(sql, con, params=params)
        finally:
            con.close()


def query(sql: str, params: Optional[Any] = None):
    """Convenience: run one tenant-scoped query using the hub-injected SQL capability."""
    return IndustryFlowSQL.from_env().query(sql, params=params)
