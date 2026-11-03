# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A stand-in for `psql`, for the backup script tests.

Every call's arguments are appended to ``$FAKE_PSQL_LOG``. The role check (``-f …roles.sql``) prints
``$FAKE_PSQL_REFUSED``: nothing means every role exists. A call that reads its script from stdin (no
``-c`` or ``-f``) appends that script to ``$FAKE_PSQL_STDIN_LOG`` when it is set, and fails when
``$FAKE_PSQL_STDIN_FAIL`` is set. Every other call succeeds and prints nothing.
"""
import os
import sys


def main(argv):
    with open(os.environ["FAKE_PSQL_LOG"], "a") as log:
        log.write(" ".join(argv) + "\n")
    if any(a.endswith("roles.sql") for a in argv):
        sys.stdout.write(os.environ.get("FAKE_PSQL_REFUSED", ""))
    if "-c" not in argv and "-f" not in argv and os.environ.get("FAKE_PSQL_STDIN_LOG"):
        with open(os.environ["FAKE_PSQL_STDIN_LOG"], "a") as log:
            log.write(sys.stdin.read())
        if os.environ.get("FAKE_PSQL_STDIN_FAIL"):
            return 3
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
