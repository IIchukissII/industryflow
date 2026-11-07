# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""scripts/box-e2e.sh's result line, run against stand-in git and docker.

scripts/box-validate.sh posts the commit status from the last `box-e2e <sha> passed|failed` line, not
from the exit code. A run that cannot remove its superuser leaves a platform-wide account behind, so
that line must say failed, and the removal must happen before it is printed.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SHA = "a" * 40

DOCKER = """#!/bin/sh
echo "$*" >> "$STUB_LOG"
case "$*" in
  *"manage_users.py delete"*) exit "${STUB_DELETE_RC:-0}" ;;
  *"manage_users.py create"*) cat >/dev/null; exit 0 ;;
  "exec "*) echo 11111111-2222-3333-4444-555555555555 ;;
  "build "*) exit "${STUB_BUILD_RC:-0}" ;;
  "run "*) exit "${STUB_SUITE_RC:-0}" ;;
esac
exit 0
"""
GIT = f"""#!/bin/sh
case "$1" in rev-parse) echo {SHA} ;; status) ;; esac
"""


def _stub(path: Path, body: str):
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def run(tmp_path):
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "box-e2e.sh", repo / "scripts" / "box-e2e.sh")
    (repo / ".box-deployed").write_text(f"deployed={SHA}\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub(bin_dir / "docker", DOCKER)
    _stub(bin_dir / "git", GIT)
    _stub(bin_dir / "hostname", "#!/bin/sh\necho 10.0.0.1\n")
    _stub(bin_dir / "stat", "#!/bin/sh\necho 999\n")
    _stub(bin_dir / "openssl", "#!/bin/sh\necho 00112233445566778899aabbccddeeff0011223344556677\n")
    log = tmp_path / "docker.log"

    def go(**rcs):
        env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", STUB_LOG=str(log),
                   **{f"STUB_{k.upper()}_RC": str(v) for k, v in rcs.items()})
        done = subprocess.run(["bash", str(repo / "scripts" / "box-e2e.sh")], env=env,
                              capture_output=True, text=True, timeout=60)
        calls = log.read_text().splitlines() if log.exists() else []
        log.unlink(missing_ok=True)
        results = [line for line in done.stdout.splitlines() if line.startswith("box-e2e ")]
        deletes = [c for c in calls if "manage_users.py delete" in c]
        return done, results, deletes

    return go


def test_a_passing_suite_whose_superuser_is_removed_passes(run):
    done, results, deletes = run()
    assert results == [f"box-e2e {SHA} passed"], done.stdout + done.stderr
    assert done.returncode == 0
    assert len(deletes) == 1, "removed once, before the result, not again on exit"


def test_a_superuser_left_behind_fails_the_run_whatever_the_suite_said(run):
    done, results, deletes = run(delete=1)
    assert results == [f"box-e2e {SHA} failed"], done.stdout + done.stderr
    assert done.returncode != 0
    assert "remove it by hand" in done.stderr
    assert deletes, "the removal was never tried"


def test_a_failing_suite_fails_and_still_removes_its_superuser(run):
    done, results, deletes = run(suite=1)
    assert results == [f"box-e2e {SHA} failed"]
    assert len(deletes) == 1


def test_a_run_that_ends_early_still_removes_its_superuser(run):
    done, results, deletes = run(build=1)
    assert done.returncode != 0 and results == []
    assert len(deletes) == 1, "the exit trap removes the superuser of a run that never got to the suite"
