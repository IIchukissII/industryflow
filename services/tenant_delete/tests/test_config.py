# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The deletion record's backup expiry is the longest any backup still holds the tenant (ADR-0033 dec 4)."""
from datetime import timedelta

import pytest

from tenant_delete import config


@pytest.mark.parametrize("env, days", [
    # Daily runs: a dump is displaced after 14 runs, a mirrored object 14 days after the next run.
    ({"TENANT_DELETE_BACKUP_COPIES": "14", "TENANT_DELETE_BACKUP_INTERVAL_DAYS": "1"}, 15),
    # Weekly runs: 14 dumps span 98 days, far beyond the mirror's ageing.
    ({"TENANT_DELETE_BACKUP_COPIES": "14", "TENANT_DELETE_BACKUP_INTERVAL_DAYS": "7"}, 98),
    ({"TENANT_DELETE_BACKUP_COPIES": "7", "TENANT_DELETE_BACKUP_INTERVAL_DAYS": "1",
      "TENANT_DELETE_MIRROR_RETAIN_DAYS": "30"}, 31),
])
def test_backup_expiry_covers_dumps_mirrors_and_archives(monkeypatch, env, days):
    monkeypatch.delenv("TENANT_DELETE_MIRROR_RETAIN_DAYS", raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert config._backup_retention() == timedelta(days=days)


def test_backup_expiry_needs_the_interval(monkeypatch):
    monkeypatch.setenv("TENANT_DELETE_BACKUP_COPIES", "14")
    monkeypatch.delenv("TENANT_DELETE_BACKUP_INTERVAL_DAYS", raising=False)
    with pytest.raises(RuntimeError, match="TENANT_DELETE_BACKUP_INTERVAL_DAYS"):
        config._backup_retention()
