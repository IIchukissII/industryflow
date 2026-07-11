# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Wiring tests for the cold-layer helper (ADR-0025 dec 5, read side) — the capability-as-bearer
plumbing, list/open/read_all ergonomics, with an injected fake transport so no broker or object
store is needed.
"""
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

    def get(self, url, params=None, verify=True, timeout=None):
        self.calls.append({"url": url, "params": params})
        if url.endswith("/cold/files"):
            return _Resp(200, json_data={"files": self.files})
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
    # Only the .parquet file is read (the _manifest.json is skipped), path joined under the query.
    assert read_paths == ["year=2026/month=07/day=06/measurements.parquet"]
    assert list(df["path"]) == ["year=2026/month=07/day=06/measurements.parquet"]
