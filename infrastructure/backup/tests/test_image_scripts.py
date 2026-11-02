# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The backup image carries every script its scripts need.

The scripts source and call each other by name (db-rehearse.sh sources db-backup.sh and runs
db-restore.sh; both source backup-lib.sh). A script the image lacks fails only when that job runs, so
the Dockerfile copies the directory's scripts whole, and every name a script sources or calls must be
one of them.
"""
import re
from pathlib import Path

DIR = Path(__file__).resolve().parents[1]
SCRIPTS = {p.name for p in DIR.glob("*.sh")}


def test_the_dockerfile_copies_every_script_not_a_list():
    copies = [line for line in (DIR / "Dockerfile").read_text().splitlines() if line.startswith("COPY ")]
    assert copies == ["COPY *.sh /usr/local/bin/"], copies


def test_every_script_a_script_sources_or_calls_is_in_the_directory():
    named = set()
    for name in SCRIPTS:
        code = "\n".join(line for line in (DIR / name).read_text().splitlines()
                          if not line.lstrip().startswith("#"))
        named |= set(re.findall(r"[\w-]+\.sh\b", code))
    assert named <= SCRIPTS, named - SCRIPTS
    # The ones this image depends on today, so a pattern that stopped matching cannot pass silently.
    assert {"backup-lib.sh", "db-backup.sh", "db-restore.sh", "notebook-backup.sh",
            "notebook-restore.sh"} <= named
