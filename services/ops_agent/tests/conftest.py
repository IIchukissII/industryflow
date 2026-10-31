# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

import sys
from pathlib import Path

import pytest

SERVICE = Path(__file__).resolve().parents[1]
REPO = SERVICE.parents[1]
sys.path.insert(0, str(SERVICE))

from config import Config  # noqa: E402


@pytest.fixture
def config(tmp_path):
    def make(**over):
        values = dict(
            webhook_secret="s" * 40, prometheus_url="http://prom", loki_url="http://loki",
            alertmanager_url="http://am",
            log_selector='{service="%s"}', runbooks_dir=REPO / "docs" / "operations", window_minutes=30,
            model_provider="anthropic", model="claude-opus-5-5", model_base_url="", model_api_key="",
            effort="medium", tools="stdio",
        )
        values.update(over)
        return Config(**values)
    return make
