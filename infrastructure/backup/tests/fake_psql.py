# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A stand-in for `psql`, for the backup script tests.

Every call's arguments are appended to ``$FAKE_PSQL_LOG``. What it prints:

* ``-f …roles.sql``: ``$FAKE_PSQL_REFUSED`` (nothing means every role exists).
* A script on stdin (the backup's snapshot session, the rehearsal's count): it follows the lines it
  is sent — ``\\o <file>`` redirects the output, ``SELECT pg_export_snapshot();`` prints a snapshot
  id, the count query (ending in ``\\gexec``) prints ``$FAKE_COUNTS`` (or, against a database named
  ``*_rehearsal``, ``$FAKE_RESTORED_COUNTS`` when set), ``\\! <cmd>`` runs the command, ``\\q`` ends.
  ``$FAKE_PSQL_FAIL_COUNT`` makes the count query fail as an error would under ON_ERROR_STOP.
"""
import os
import subprocess
import sys

SNAPSHOT = "00000003-0000001B-1"
DEFAULT_COUNTS = "public.companies\t2\npublic.user\t1\n"


def main(argv):
    with open(os.environ["FAKE_PSQL_LOG"], "a") as log:
        log.write(" ".join(argv) + "\n")
    if any(a.endswith("roles.sql") for a in argv):
        sys.stdout.write(os.environ.get("FAKE_PSQL_REFUSED", ""))
        return 0
    if "-f" in argv and argv[argv.index("-f") + 1] == "-" or ("-c" not in argv and "-f" not in argv):
        return session(argv)
    return 0


def session(argv):
    db = argv[argv.index("-d") + 1] if "-d" in argv else ""
    out = sys.stdout
    statement = []
    for line in sys.stdin:
        line = line.rstrip("\n")
        if line.startswith("\\o"):
            if out is not sys.stdout:
                out.close()
            path = line[2:].strip()
            out = open(path, "w") if path else sys.stdout
        elif line.startswith("\\!"):
            out.flush()
            subprocess.run(line[2:].strip(), shell=True, check=False)
        elif line.startswith("\\q"):
            break
        elif line.strip() == "SELECT pg_export_snapshot();":
            out.write(SNAPSHOT + "\n")
        else:
            statement.append(line)
            if line.endswith("\\gexec"):
                if os.environ.get("FAKE_PSQL_FAIL_COUNT"):
                    print("ERROR:  permission denied for table x", file=sys.stderr)
                    return 3
                restored = os.environ.get("FAKE_RESTORED_COUNTS")
                out.write(restored if restored is not None and db.endswith("_rehearsal")
                          else os.environ.get("FAKE_COUNTS", DEFAULT_COUNTS))
                statement = []
        out.flush()
    if out is not sys.stdout:
        out.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
