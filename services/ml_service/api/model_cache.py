# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Warm model cache.

MLflow model loads are slow (artifact fetch + deserialize). Both the real-time anomaly
detector (`/api/inference`) and the ADR-0021 drift evaluator score with the same model
repeatedly, so cold-loading it from MLflow on every call is wasteful. This is a small
process-local LRU cache of already-loaded model objects keyed by MLflow run_id: the first
call warms it, subsequent calls reuse the warm model.

Scope note: the cache is per worker process (uvicorn runs several). That is the intended
granularity — each worker warms independently; there is no shared mutable model state to
coordinate. Bounded by MODEL_CACHE_SIZE (default 8) with LRU eviction so a tenant with
many models cannot grow it without limit.
"""

from __future__ import annotations

import logging
import os
import threading
from collections import OrderedDict
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)

_MAX_ENTRIES = max(1, int(os.getenv("MODEL_CACHE_SIZE", "8")))

_cache: "OrderedDict[str, Any]" = OrderedDict()
_lock = threading.Lock()


def get_or_load(key: str, loader: Callable[[str], Any]) -> Any:
    """Return the cached model for ``key``, loading + caching it via ``loader`` on a miss.

    ``loader`` is invoked outside the lock (it may block on MLflow I/O) so a slow load does
    not stall cache hits for other models. A rare concurrent double-load of the same key is
    harmless — last write wins and both callers get a valid model.
    """
    with _lock:
        cached = _cache.get(key)
        if cached is not None:
            _cache.move_to_end(key)  # mark most-recently-used
            return cached

    model = loader(key)

    with _lock:
        _cache[key] = model
        _cache.move_to_end(key)
        while len(_cache) > _MAX_ENTRIES:
            evicted, _ = _cache.popitem(last=False)  # least-recently-used
            logger.info("Evicted model %s from warm cache (max=%d)", evicted, _MAX_ENTRIES)

    return model


def invalidate(key: str) -> None:
    """Drop a model from the cache (e.g. after a redeploy of the same run_id)."""
    with _lock:
        _cache.pop(key, None)


def clear() -> None:
    """Empty the cache (tests / shutdown)."""
    with _lock:
        _cache.clear()


def info() -> Dict[str, Any]:
    """Introspect the cache (size / capacity / keys) for tests + a health probe."""
    with _lock:
        keys: List[str] = list(_cache.keys())
    return {"size": len(keys), "max": _MAX_ENTRIES, "keys": keys}
