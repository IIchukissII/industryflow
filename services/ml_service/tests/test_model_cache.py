# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Unit tests for the warm model cache (inference + drift share a warm model).

Pure — a counting fake loader stands in for the slow MLflow load; no MLflow needed.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

import model_cache  # noqa: E402


@pytest.fixture(autouse=True)
def _clear():
    model_cache.clear()
    yield
    model_cache.clear()


def test_first_call_loads_then_warm_hits_reuse():
    calls = {"n": 0}

    def loader(key):
        calls["n"] += 1
        return f"model::{key}"

    m1 = model_cache.get_or_load("run-a", loader)
    m2 = model_cache.get_or_load("run-a", loader)
    assert m1 == m2 == "model::run-a"
    assert calls["n"] == 1  # loaded once, second call was a warm hit
    assert model_cache.info()["size"] == 1


def test_distinct_keys_load_separately():
    calls = {"n": 0}

    def loader(key):
        calls["n"] += 1
        return key

    model_cache.get_or_load("a", loader)
    model_cache.get_or_load("b", loader)
    model_cache.get_or_load("a", loader)  # warm
    assert calls["n"] == 2
    assert set(model_cache.info()["keys"]) == {"a", "b"}


def test_lru_eviction_respects_max(monkeypatch):
    # Rebuild the cache internals with a tiny capacity.
    monkeypatch.setattr(model_cache, "_MAX_ENTRIES", 2)
    model_cache.clear()

    loader = lambda k: k  # noqa: E731
    model_cache.get_or_load("a", loader)
    model_cache.get_or_load("b", loader)
    model_cache.get_or_load("a", loader)   # touch 'a' → most-recently-used
    model_cache.get_or_load("c", loader)   # evicts LRU, which is 'b'

    keys = set(model_cache.info()["keys"])
    assert keys == {"a", "c"}
    assert "b" not in keys


def test_invalidate_forces_reload():
    calls = {"n": 0}

    def loader(key):
        calls["n"] += 1
        return calls["n"]

    first = model_cache.get_or_load("a", loader)
    model_cache.invalidate("a")
    second = model_cache.get_or_load("a", loader)
    assert first == 1 and second == 2  # reloaded after invalidation


def test_info_reports_capacity():
    assert model_cache.info()["max"] >= 1
