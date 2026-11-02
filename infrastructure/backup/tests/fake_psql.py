# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A stand-in for `psql`, for the backup script tests.

Every call's arguments are appended to ``$FAKE_PSQL_LOG``. The role check (``-f …roles.sql``) prints
``$FAKE_PSQL_REFUSED``: nothing means every role exists. Every other call succeeds and prints nothing.
"""
import os
import sys


def main(argv):
    with open(os.environ["FAKE_PSQL_LOG"], "a") as log:
        log.write(" ".join(argv) + "\n")
    if any(a.endswith("roles.sql") for a in argv):
        sys.stdout.write(os.environ.get("FAKE_PSQL_REFUSED", ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
