# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The worker refuses retrain-recommendation settings that cannot hold ADR-0022 dec 4."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import AlertServiceConfig  # noqa: E402


def _config(**settings):
    config = AlertServiceConfig()
    for key, value in settings.items():
        setattr(config, key, value)
    return config


def test_the_defaults_are_valid(monkeypatch):
    monkeypatch.delenv("RETRAIN_RECO_SEVERITY", raising=False)
    assert _config().retrain_settings_invalid() == []


@pytest.mark.parametrize("settings, reason", [
    ({"RETRAIN_MIN_LABELS": 0}, "RETRAIN_MIN_LABELS must be at least 1"),
    ({"RETRAIN_PRECISION_FLOOR": 0.0}, "RETRAIN_PRECISION_FLOOR must be in (0, 1]"),
    ({"RETRAIN_PRECISION_FLOOR": 1.5}, "RETRAIN_PRECISION_FLOOR must be in (0, 1]"),
    ({"RETRAIN_MIN_DRIFT_ALERTS": 0}, "RETRAIN_MIN_DRIFT_ALERTS must be at least 1"),
])
def test_a_setting_outside_its_range_is_refused(monkeypatch, settings, reason):
    monkeypatch.delenv("RETRAIN_RECO_SEVERITY", raising=False)
    [invalid] = _config(**settings).retrain_settings_invalid()
    assert invalid.startswith(reason)


def test_a_removed_setting_still_set_is_refused(monkeypatch):
    # The severity is decided, high; a deployment that still sets it would be silently ignored.
    monkeypatch.setenv("RETRAIN_RECO_SEVERITY", "low")
    assert _config().retrain_settings_invalid() == ["RETRAIN_RECO_SEVERITY is no longer a setting"]


def test_validate_exits_on_an_invalid_setting(monkeypatch):
    monkeypatch.delenv("RETRAIN_RECO_SEVERITY", raising=False)
    config = _config(RETRAIN_MIN_LABELS=0)
    for key in config.REQUIRED_ENV:
        setattr(config, key, "set")
    with pytest.raises(SystemExit):
        config.validate()
