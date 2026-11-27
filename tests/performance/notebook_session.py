# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The reference journey of an authoring session (docs/operations/capacity.md#a-notebook-session,
ADR-0050 dec 5), run inside the session's container: a kernel that stays alive runs every code cell of a
notebook, then waits for the marker file /tmp/use-now, then carries on with the same data (the features
and the fit again, a summary of the frame), then holds its state until stopped.

    python notebook_session.py <notebook.ipynb>

Prints `notebook-done <epoch>`, `use-start <epoch>` and `use-done <epoch>`. The marker is created once the
session's untouched pages were reclaimed, so the use step brings back only the pages it uses.
"""
import json
import pathlib
import sys
import time

from jupyter_client.manager import start_new_kernel

MARKER = pathlib.Path("/tmp/use-now")


def run(kc, code):
    reply = kc.execute_interactive(code, timeout=900, output_hook=lambda m: None)
    if reply["content"]["status"] != "ok":
        sys.exit(f"cell failed: {reply['content'].get('ename')} {reply['content'].get('evalue')}")


def main() -> None:
    cells = ["".join(c["source"]) for c in json.load(open(sys.argv[1]))["cells"] if c["cell_type"] == "code"]
    _km, kc = start_new_kernel(kernel_name="python3")
    for c in cells:
        run(kc, c)
    print("notebook-done", int(time.time()), flush=True)
    while not MARKER.exists():
        time.sleep(1)
    print("use-start", int(time.time()), flush=True)
    run(kc, cells[2])  # the features, over the same frame
    run(kc, cells[3])  # the fit, again
    run(kc, "df.groupby('sensor')['value'].describe()")
    print("use-done", int(time.time()), flush=True)
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
