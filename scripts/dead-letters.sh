#!/bin/sh
# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# Dead-letter inspection and replay (ADR-0005; docs/operations/dead-letters.md).
#
# Runs inside the Kafka container, with its console tools and python3; nothing is installed:
#   docker compose exec -T kafka sh -s -- <command> < scripts/dead-letters.sh
#   kubectl exec -i <kafka-pod> -- sh -s -- <command> < scripts/dead-letters.sh
#
#   topics                          every dead-letter topic and its letter count per partition
#   show <topic> [-p P] [-o N] [-n M]
#                                   letters from offset N (default 0) of partition P (default 0),
#                                   at most M (default 50): offset, reason, consumer, tenant, sensor,
#                                   reading time
#   replay <topic> -o N -t M [-p P] [-r REASON] [--yes]
#                                   put the original reading of each letter from offset N to M of
#                                   partition P back on the topic it came from, keyed by its sensor
#                                   as ingestion keys it; only letters with that reason if -r. Without
#                                   --yes it prints what it would replay and sends nothing. A letter
#                                   whose original is not a reading with a sensor is never replayed.
#
# Every letter, from either producer, is one JSON envelope: original (the message text), reason,
# consumer, topic, partition, offset. Replay is safe to repeat: the consumers of the source topic
# are idempotent (ADR-0005 dec 6, ADR-0006 dec 3). It does not remove letters; a topic only grows.
set -eu

BOOTSTRAP="${BOOTSTRAP:-localhost:9092}"

die() { echo "dead-letters: $*" >&2; exit 2; }

letters() {  # <topic> <partition> <offset> <max>
  kafka-console-consumer --bootstrap-server "$BOOTSTRAP" --topic "$1" --partition "$2" \
    --offset "$3" --max-messages "$4" --timeout-ms "${TIMEOUT_MS:-10000}" \
    --property print.offset=true 2>/dev/null || true
}

PY_SHOW='
import json, sys
for line in sys.stdin:
    head, _, value = line.rstrip("\n").partition("\t")
    offset = head.split(":", 1)[1] if head.startswith("Offset:") else "?"
    try:
        env = json.loads(value)
    except ValueError:
        env = {}
    try:
        reading = json.loads(env.get("original") or "null")
    except ValueError:
        reading = None
    reading = reading if isinstance(reading, dict) else {}
    print("\t".join(str(x) for x in (
        offset, env.get("reason", "?"), env.get("consumer", "?"), reading.get("company_id", "-"),
        reading.get("sensor_id", "-"), reading.get("timestamp", "-"))))
'

# Emits "<key>\t<original>" per replayable letter on stdout, a summary on stderr. argv: source
# topic the letters must come from (set on the first letter), reason filter.
PY_REPLAY='
import json, sys
want = sys.argv[1] or None
source, sent, skipped = None, 0, []
for line in sys.stdin:
    head, _, value = line.rstrip("\n").partition("\t")
    offset = head.split(":", 1)[1] if head.startswith("Offset:") else "?"
    try:
        env = json.loads(value)
    except ValueError:
        skipped.append((offset, "not a dead-letter envelope")); continue
    if want and env.get("reason") != want:
        continue
    try:
        reading = json.loads(env.get("original") or "null")
    except ValueError:
        reading = None
    if not isinstance(reading, dict) or not reading.get("sensor_id"):
        skipped.append((offset, "original is not a reading with a sensor")); continue
    if source is None:
        source = env.get("topic")
    if env.get("topic") != source:
        skipped.append((offset, "came from another topic than the first letter")); continue
    print(str(reading["sensor_id"]) + "\t" + env["original"])
    sent += 1
print("source=%s replayable=%d skipped=%d" % (source or "-", sent, len(skipped)), file=sys.stderr)
for offset, why in skipped:
    print("  skipped offset %s: %s" % (offset, why), file=sys.stderr)
'

cmd_topics() {
  kafka-topics --bootstrap-server "$BOOTSTRAP" --list | grep '\.dlq$' | while read -r t; do
    kafka-get-offsets --bootstrap-server "$BOOTSTRAP" --topic "$t" 2>/dev/null \
      | awk -F: '{ printf "%s\tpartition %s\t%s letter(s)\n", $1, $2, $3 }'
  done
}

cmd_show() {
  topic="${1:-}"; [ -n "$topic" ] || die "usage: show <topic> [-p P] [-o N] [-n M]"; shift
  p=0; o=0; n=50
  while [ $# -gt 0 ]; do
    case "$1" in -p) p="$2" ;; -o) o="$2" ;; -n) n="$2" ;; *) die "unknown option $1" ;; esac
    shift 2
  done
  printf 'offset\treason\tconsumer\ttenant\tsensor\treading time\n'
  letters "$topic" "$p" "$o" "$n" | python3 -c "$PY_SHOW"
}

cmd_replay() {
  topic="${1:-}"; [ -n "$topic" ] || die "usage: replay <topic> -o N -t M [-p P] [-r REASON] [--yes]"; shift
  case "$topic" in *.dlq) ;; *) die "$topic is not a dead-letter topic" ;; esac
  p=0; o=""; t=""; reason=""; yes=""
  while [ $# -gt 0 ]; do
    case "$1" in
      -p) p="$2"; shift 2 ;; -o) o="$2"; shift 2 ;; -t) t="$2"; shift 2 ;;
      -r) reason="$2"; shift 2 ;; --yes) yes=1; shift ;; *) die "unknown option $1" ;;
    esac
  done
  [ -n "$o" ] && [ -n "$t" ] || die "replay needs an explicit range: -o <first offset> -t <last offset>"
  [ "$t" -ge "$o" ] || die "the last offset is before the first"
  tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
  letters "$topic" "$p" "$o" $((t - o + 1)) | python3 -c "$PY_REPLAY" "$reason" > "$tmp/out" 2> "$tmp/summary"
  cat "$tmp/summary" >&2
  source=$(sed -n '1s/^source=\([^ ]*\).*/\1/p' "$tmp/summary")
  if [ ! -s "$tmp/out" ]; then echo "dead-letters: nothing to replay"; return 0; fi
  case "$source" in ""|-|*.dlq) die "refusing to replay onto '$source'" ;; esac
  if [ -z "$yes" ]; then
    echo "dead-letters: would replay $(wc -l < "$tmp/out") reading(s) onto $source; add --yes to send"
    return 0
  fi
  kafka-console-producer --bootstrap-server "$BOOTSTRAP" --topic "$source" \
    --property parse.key=true --property key.separator="$(printf '\t')" < "$tmp/out"
  echo "dead-letters: replayed $(wc -l < "$tmp/out") reading(s) from $topic $p:$o-$t onto $source"
}

sub="${1:-}"; shift 2>/dev/null || true
case "$sub" in
  topics) cmd_topics ;;
  show)   cmd_show "$@" ;;
  replay) cmd_replay "$@" ;;
  *) echo "usage: dead-letters.sh {topics|show <topic> ...|replay <topic> -o N -t M ...}" >&2; exit 2 ;;
esac
