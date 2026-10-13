# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Wiring tests for the cold-layer helper (ADR-0025 dec 5, read side) — the capability-as-bearer
plumbing, list/open/read_all ergonomics, with an injected fake transport so no broker or object
store is needed.
"""
import json

import pytest

from industryflow import IndustryFlowCold
from industryflow import cold as coldmod


class _Resp:
    def __init__(self, status=200, json_data=None, content=b""):
        self.status_code = status
        self._json = json_data
        self.content = content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json


class _FakeSession:
    def __init__(self):
        self.headers = {}
        self.calls = []
        self.files = [
            {"path": "year=2026/month=07/day=06/measurements.parquet", "size": 10},
            {"path": "year=2026/month=07/day=06/_manifest.json", "size": 2},
        ]
        self.content = b"PARQUET-BYTES"
        self.manifests = {"year=2026/month=07/day=06/_manifest.json": {"rows": 10}}

    def get(self, url, params=None, verify=True, timeout=None):
        self.calls.append({"url": url, "params": params})
        if url.endswith("/cold/files"):
            return _Resp(200, json_data={"files": self.files})
        key = url.split("/cold/object/", 1)[-1]
        if key in self.manifests:
            return _Resp(200, content=json.dumps(self.manifests[key]).encode())
        return _Resp(200, content=self.content)


def test_capability_sent_as_bearer():
    s = _FakeSession()
    IndustryFlowCold("http://broker:5060", "cap-xyz", session=s)
    assert s.headers["Authorization"] == "Bearer cap-xyz"


def test_from_env_reads_hub_injected_vars(monkeypatch):
    monkeypatch.setenv(coldmod.BROKER_URL_ENV, "http://notebook-cold-broker:5060")
    monkeypatch.setenv(coldmod.CAPABILITY_ENV, "cap-from-env")
    s = _FakeSession()
    cold = IndustryFlowCold.from_env(session=s)
    assert cold.list_files() == s.files
    assert s.calls[0]["url"] == "http://notebook-cold-broker:5060/cold/files"
    assert s.calls[0]["params"] == {"path": ""}
    assert s.headers["Authorization"] == "Bearer cap-from-env"


def test_from_env_without_capability_is_a_clear_error(monkeypatch):
    monkeypatch.setenv(coldmod.BROKER_URL_ENV, "http://broker:5060")
    monkeypatch.delenv(coldmod.CAPABILITY_ENV, raising=False)
    with pytest.raises(RuntimeError, match="authoring notebook"):
        IndustryFlowCold.from_env()


def test_open_fetches_object_bytes():
    s = _FakeSession()
    cold = IndustryFlowCold("http://broker:5060", "cap", session=s)
    data = cold.open("year=2026/month=07/day=06/measurements.parquet")
    assert data == b"PARQUET-BYTES"
    assert s.calls[-1]["url"] == "http://broker:5060/cold/object/year=2026/month=07/day=06/measurements.parquet"


def test_empty_args_rejected():
    with pytest.raises(ValueError, match="broker URL"):
        IndustryFlowCold("", "cap")
    with pytest.raises(ValueError, match="capability"):
        IndustryFlowCold("http://broker:5060", "")


def test_read_all_selects_parquet_and_concatenates(monkeypatch):
    import pandas as pd

    s = _FakeSession()
    cold = IndustryFlowCold("http://broker:5060", "cap", session=s)
    # Stub the per-file parquet read (no pyarrow needed in the client test env); record the paths.
    read_paths = []

    def fake_read_parquet(path):
        read_paths.append(path)
        return pd.DataFrame({"path": [path]})

    monkeypatch.setattr(cold, "read_parquet", fake_read_parquet)
    df = cold.read_all()
    # Only the part the (single-file) manifest stands for is read; the manifest itself is not a part.
    assert read_paths == ["year=2026/month=07/day=06/measurements.parquet"]
    assert list(df["path"]) == ["year=2026/month=07/day=06/measurements.parquet"]


def _reader(monkeypatch, files, manifests):
    import pandas as pd

    s = _FakeSession()
    s.files = [{"path": f, "size": 1} for f in files]
    s.manifests = manifests
    cold = IndustryFlowCold("http://broker:5060", "cap", session=s)
    read_paths = []

    def fake_read_parquet(path):
        read_paths.append(path)
        return pd.DataFrame({"path": [path]})

    monkeypatch.setattr(cold, "read_parquet", fake_read_parquet)
    return cold, read_paths


DAY6 = "year=2026/month=07/day=06"
DAY7 = "year=2026/month=07/day=07"


def test_read_all_reads_every_part_a_manifest_lists(monkeypatch):
    # A day exported, dropped, and later topped up with late rows holds two parts. Both are history;
    # reading only the first would silently lose the late rows.
    cold, read_paths = _reader(
        monkeypatch,
        [f"{DAY6}/measurements.parquet", f"{DAY6}/measurements-0001.parquet", f"{DAY6}/_manifest.json"],
        {f"{DAY6}/_manifest.json": {"format": 2, "rows": 7, "parts": [
            {"file": "measurements.parquet", "rows": 5}, {"file": "measurements-0001.parquet", "rows": 2}]}},
    )
    cold.read_all()
    assert read_paths == [f"{DAY6}/measurements.parquet", f"{DAY6}/measurements-0001.parquet"]


def test_read_all_skips_parquet_no_manifest_lists(monkeypatch):
    # A part being written (no manifest entry yet), and a day whose export is not verified yet.
    cold, read_paths = _reader(
        monkeypatch,
        [f"{DAY6}/measurements.parquet", f"{DAY6}/measurements-0001.parquet", f"{DAY6}/_manifest.json",
         f"{DAY7}/measurements.parquet"],
        {f"{DAY6}/_manifest.json": {"format": 2, "rows": 5,
                                     "parts": [{"file": "measurements.parquet", "rows": 5}]}},
    )
    cold.read_all()
    assert read_paths == [f"{DAY6}/measurements.parquet"]


def test_read_all_under_a_narrowed_path(monkeypatch):
    cold, read_paths = _reader(
        monkeypatch,
        ["day=06/measurements.parquet", "day=06/_manifest.json"],
        {"year=2026/month=07/day=06/_manifest.json": {"rows": 3}},
    )
    cold.read_all("year=2026/month=07")
    assert read_paths == ["year=2026/month=07/day=06/measurements.parquet"]


def test_read_all_refuses_a_manifest_naming_a_missing_part(monkeypatch):
    cold, _ = _reader(
        monkeypatch,
        [f"{DAY6}/measurements.parquet", f"{DAY6}/_manifest.json"],
        {f"{DAY6}/_manifest.json": {"format": 2, "rows": 7, "parts": [
            {"file": "measurements.parquet", "rows": 5}, {"file": "measurements-0001.parquet", "rows": 2}]}},
    )
    with pytest.raises(RuntimeError, match="not in the store"):
        cold.read_all()
