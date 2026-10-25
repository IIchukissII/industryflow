# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
scripts/dead-letters.sh replays a reading as ingestion sent it, and only a reading (ADR-0005).

Runs the real script against stand-in console tools on PATH: the consumer prints the letters as
`kafka-console-consumer --property print.offset=true` does, the producer records what it was given.
A replay must re-key each original by its sensor, put it back on the topic the letter names, honour
the reason filter, skip letters that hold no reading, send nothing without --yes, and never write
onto a dead-letter topic.
"""
import json
import os
import stat
import subprocess
import uuid
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "dead-letters.sh"
TENANT = str(uuid.uuid4())


def _letter(offset, reason, original, topic="sensor-data-raw"):
    return f"Offset:{offset}\t" + json.dumps({
        "original": original, "reason": reason, "consumer": "spark-streaming",
        "topic": topic, "partition": 0, "offset": 100 + offset,
    })


def _reading(sensor):
    return json.dumps({"timestamp": "2026-10-25T10:00:00+00:00", "sensor_id": sensor,
                       "company_id": TENANT, "value": 1.5})


@pytest.fixture
def kafka(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    letters = tmp_path / "letters"
    sent = tmp_path / "sent"
    tools = {
        # Prints the letters from --offset on, at most --max-messages, as the console consumer does.
        "kafka-console-consumer": f"""#!/bin/sh
off=0; max=1000000
while [ $# -gt 0 ]; do case "$1" in --offset) off="$2"; shift 2 ;; --max-messages) max="$2"; shift 2 ;; *) shift ;; esac; done
awk -F'\\t' -v off="$off" -v max="$max" '{{ split($1, h, ":"); if (h[2] >= off && n < max) {{ print; n++ }} }}' "{letters}"
""",
        "kafka-console-producer": f"""#!/bin/sh
topic=""
while [ $# -gt 0 ]; do case "$1" in --topic) topic="$2"; shift 2 ;; *) shift ;; esac; done
while IFS= read -r line; do printf '%s\\t%s\\n' "$topic" "$line" >> "{sent}"; done
""",
    }
    for name, body in tools.items():
        path = bin_dir / name
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)

    def run(*args, lines=()):
        letters.write_text("".join(line + "\n" for line in lines))
        return subprocess.run(["sh", str(SCRIPT), *args], capture_output=True, text=True,
                              env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"})

    def sent_lines():
        return [line.split("\t", 2) for line in sent.read_text().splitlines()] if sent.exists() else []

    return run, sent_lines


LETTERS = [
    _letter(0, "unknown_sensor", _reading("s-1")),
    _letter(1, "missing_or_malformed_field", "{not json"),
    _letter(2, "unknown_sensor", _reading("s-2")),
    _letter(3, "suspended_tenant", _reading("s-3")),
]


def test_show_lists_each_letter_with_its_reason_and_reading(kafka):
    run, _ = kafka
    r = run("show", "sensor-data-raw.measurements.dlq", lines=LETTERS)
    assert r.returncode == 0, r.stderr
    rows = [line.split("\t") for line in r.stdout.splitlines()[1:]]
    assert [row[:2] for row in rows] == [["0", "unknown_sensor"], ["1", "missing_or_malformed_field"],
                                          ["2", "unknown_sensor"], ["3", "suspended_tenant"]]
    assert rows[0][3:5] == [TENANT, "s-1"] and rows[1][3:5] == ["-", "-"]


def test_replay_without_yes_sends_nothing(kafka):
    run, sent = kafka
    r = run("replay", "sensor-data-raw.measurements.dlq", "-o", "0", "-t", "3", lines=LETTERS)
    assert r.returncode == 0, r.stderr
    assert "would replay 3 reading(s) onto sensor-data-raw" in r.stdout
    assert sent() == []


def test_replay_rekeys_by_sensor_onto_the_source_and_skips_what_is_not_a_reading(kafka):
    run, sent = kafka
    r = run("replay", "sensor-data-raw.measurements.dlq", "-o", "0", "-t", "3", "--yes", lines=LETTERS)
    assert r.returncode == 0, r.stderr
    assert [(topic, key) for topic, key, _ in sent()] == [
        ("sensor-data-raw", "s-1"), ("sensor-data-raw", "s-2"), ("sensor-data-raw", "s-3")]
    assert json.loads(sent()[0][2]) == json.loads(_reading("s-1"))
    assert "skipped offset 1: original is not a reading with a sensor" in r.stderr


def test_replay_honours_the_range_and_the_reason(kafka):
    run, sent = kafka
    r = run("replay", "sensor-data-raw.measurements.dlq", "-o", "1", "-t", "3", "-r", "unknown_sensor", "--yes",
            lines=LETTERS)
    assert r.returncode == 0, r.stderr
    assert [key for _, key, _ in sent()] == ["s-2"]


@pytest.mark.parametrize("args", [
    ("replay", "sensor-data-raw", "-o", "0", "-t", "1", "--yes"),       # not a dead-letter topic
    ("replay", "sensor-data-raw.measurements.dlq", "--yes"),            # no explicit range
    ("replay", "sensor-data-raw.measurements.dlq", "-o", "3", "-t", "1"),  # range backwards
])
def test_replay_refuses_what_it_cannot_do_safely(kafka, args):
    run, sent = kafka
    r = run(*args, lines=LETTERS)
    assert r.returncode != 0
    assert sent() == []


def test_replay_never_writes_onto_a_dead_letter_topic(kafka):
    run, sent = kafka
    looped = [_letter(0, "unknown_sensor", _reading("s-1"), topic="sensor-data-raw.measurements.dlq")]
    r = run("replay", "sensor-data-raw.measurements.dlq", "-o", "0", "-t", "0", "--yes", lines=looped)
    assert r.returncode != 0 and "refusing to replay" in r.stderr
    assert sent() == []


def test_ci_runs_this_file_when_the_script_changes():
    wf = (SCRIPT.parents[1] / ".github" / "workflows" / "unit-tests.yml").read_text()
    here = "scripts/tests/test_dead_letters.py"
    assert f"path: {here}" in wf
    assert "- 'scripts/dead-letters.sh'" in wf
    assert f"- '{here}'" in wf
