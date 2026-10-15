# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Every pinned requirements file is one the bot can see, and the bot watches nothing that is gone.

Requirements are pinned `==` (lint-enforced), so a file in a directory no pip entry lists never moves:
it keeps its pins while every other copy of the same library is bumped, and nothing reports it.
"""

from __future__ import annotations

from _repo import ROOT, load_yaml

SKIP = {"node_modules", ".git", ".venv", "venv"}


def _watched() -> set[str]:
    dirs: set[str] = set()
    for update in load_yaml(".github/dependabot.yml")["updates"]:
        if update["package-ecosystem"] == "pip":
            dirs.update(update.get("directories") or [update["directory"]])
    return dirs


def _pinned() -> set[str]:
    return {
        "/" + str(p.parent.relative_to(ROOT))
        for p in ROOT.rglob("requirements*.txt")
        if not SKIP.intersection(p.relative_to(ROOT).parts)
    }


def test_the_guard_reads_both_sides():
    assert len(_watched()) >= 10 and len(_pinned()) >= 10


def test_every_requirements_directory_is_watched():
    missing = sorted(_pinned() - _watched())
    assert not missing, f"requirements files the bot never updates: {missing}"


def test_every_watched_directory_still_has_requirements():
    stale = sorted(_watched() - _pinned())
    assert not stale, f"dependabot pip directories with no requirements file: {stale}"
