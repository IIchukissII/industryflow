# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The stateful-feature kill-switch (ADR-0024 rev 1).

One global, operator-flipped flag: *are stateful features enabled?* When it is off, the engine
fills each stateful transform's slot with its neutral value **without calling the transform**
(ADR-0024 dec 3-4), so a degraded aggregate substrate stops being queried N times per inference.
Relieving the dependency is the point — nulling the result *after* making the call would protect
the model and do nothing for the database.

**Why a DB-backed switch guarding a DB-backed read is not circular in the way it looks** (dec 7):
the incident this exists for is a database that is *alive but overloaded* — in part by the very
baseline queries the switch cuts. There, one tiny cached config row still reads fine while the
hypertable queries time out. A *totally unreachable* database is a different incident and not this
switch's: the switch read fails too, reads as enabled, the baseline reads fail, and the transform's
own fallback returns the neutral anyway. Inference keeps serving either way.

**It fails open, on last-known-good.** A failed read holds the previous value rather than evicting
it; a switch that has never read successfully is `enabled`. Failing *closed* would be auto-trip in
disguise — a transient blip would neutralize a feature class with no operator deciding anything,
and it would flap. ADR-0024 dec 6 defers auto-trip precisely because it needs hysteresis and is its
own decision. So this switch only ever kills features because a human turned it off.
"""
import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

SWITCH_KEY = "stateful_features_enabled"

# The flag is platform-global, so it lives in the shared schema, not a tenant one (ADR-0003).
# Qualified as `public.` deliberately: the inference path may hold a tenant `search_path`, under
# which a bare `platform_config` would resolve into the tenant schema and fail.
_SQL = "SELECT value FROM public.platform_config WHERE key = $1"


class StatefulFeatureSwitch:
    """Reads the global stateful-feature flag, cached, fail-open."""

    def __init__(self, pool, cache_ttl_seconds: Optional[float] = None,
                 query_timeout_seconds: Optional[float] = None):
        """
        Args:
            pool: asyncpg connection pool (shared with the repository).
            cache_ttl_seconds: how long a read is reused before refreshing. Bounds how long an
                operator waits for a flip to take effect, so it is short — the switch is an
                incident control and a minute of lag would be a minute of the incident.
                Defaults to the STATEFUL_SWITCH_CACHE_TTL env var (default 5.0).
            query_timeout_seconds: per-query timeout, so reading the switch cannot itself become a
                slow call on the inference hot path. Defaults to STATEFUL_SWITCH_QUERY_TIMEOUT
                (default 1.0).
        """
        self.pool = pool
        self.cache_ttl_seconds = cache_ttl_seconds if cache_ttl_seconds is not None else float(
            os.getenv("STATEFUL_SWITCH_CACHE_TTL", "5.0")
        )
        self.query_timeout_seconds = query_timeout_seconds if query_timeout_seconds is not None else float(
            os.getenv("STATEFUL_SWITCH_QUERY_TIMEOUT", "1.0")
        )
        # Last successfully-read value. `True` before any read: a switch that has never been read
        # is enabled (fail-open, dec 7) — the platform's normal state is that features compute.
        self._value = True
        self._expires_at = 0.0
        self._ever_read = False

    async def enabled(self) -> bool:
        """Whether stateful features are enabled. Never raises — a broken read holds the last
        known value, so the switch degrades to 'carry on as before', never to a surprise kill."""
        now = time.monotonic()
        if now < self._expires_at:
            return self._value

        value = await self._read()
        if value is None:
            # Failed read: hold the last known value rather than evicting it, and back off for one
            # TTL so a degraded DB is not re-queried on every single inference — the switch must
            # not become the load it exists to relieve.
            self._expires_at = now + self.cache_ttl_seconds
            if self._ever_read:
                logger.warning("Kill-switch read failed; holding last known value (enabled=%s)",
                               self._value)
            else:
                logger.warning("Kill-switch has never been read; assuming enabled (fail-open)")
            return self._value

        if self._ever_read and value != self._value:
            logger.warning("Stateful-feature kill-switch flipped: enabled=%s -> %s",
                           self._value, value)
        self._value = value
        self._ever_read = True
        self._expires_at = now + self.cache_ttl_seconds
        return value

    async def _read(self) -> Optional[bool]:
        """The flag from the DB, or ``None`` if it could not be read (any reason)."""
        try:
            async with self.pool.acquire() as conn:
                value = await conn.fetchval(_SQL, SWITCH_KEY, timeout=self.query_timeout_seconds)
        except Exception as e:  # noqa: BLE001 — a switch that raises would take inference down
            logger.warning("Kill-switch query failed: %s", e)
            return None

        if value is None:
            # The row is absent (migration not applied, or someone deleted it). Enabled is the
            # documented default; say so once per TTL rather than silently assuming it.
            logger.warning("Kill-switch row '%s' not found in public.platform_config; "
                           "assuming enabled", SWITCH_KEY)
            return True

        # JSONB comes back as a string through asyncpg unless a codec is registered; accept both
        # the parsed bool and the raw JSON text rather than depending on the pool's configuration.
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            text = value.strip().strip('"').lower()
            if text in ("true", "false"):
                return text == "true"
        logger.warning("Kill-switch value %r is not a boolean; assuming enabled", value)
        return True
