# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A sample device: streams readings for provisioned sensors through the ingest edge over mutual TLS.

It is a device and nothing else (ADR-0002): it holds a device certificate, verifies the edge, and
sends readings for the equipment and sensor ids it is given. It signs in as no user and provisions
nothing. Each sensor's value moves smoothly within the range given for it.

    python3 clients/device/stream_readings.py --url https://<host>:8443/ingest \\
        --cert <chain.crt> --key <key> --ca <edge-ca.crt> \\
        --equipment-id <uuid> --site-id <site> \\
        --sensor <name>=<sensor-uuid>:<low>:<high> [--sensor ...] [--interval 1] [--count N]

Standard library only, beside the platform's contracts package (libs/industryflow_contracts).
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, List, Optional

from industryflow_contracts import readings


@dataclass(frozen=True)
class Sensor:
    name: str
    sensor_id: str
    low: float
    high: float


def parse_sensor(text: str) -> Sensor:
    """``<name>=<sensor-uuid>:<low>:<high>``."""
    try:
        name, rest = text.split("=", 1)
        sensor_id, low, high = rest.rsplit(":", 2)
        sensor = Sensor(name, sensor_id, float(low), float(high))
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not <name>=<sensor-uuid>:<low>:<high>") from None
    if not (sensor.name and sensor.sensor_id) or not sensor.low < sensor.high:
        raise argparse.ArgumentTypeError(f"{text!r}: a name, an id and a range with low < high")
    return sensor


def value(sensor: Sensor, step: int, rng: random.Random) -> float:
    """A slow wave across the sensor's range, with a little noise, staying inside it."""
    mid, half = (sensor.low + sensor.high) / 2, (sensor.high - sensor.low) / 2
    phase = zlib.crc32(sensor.name.encode()) % 360
    wave = math.sin(math.radians(phase + step * 6))
    return round(min(sensor.high, max(sensor.low, mid + half * (0.8 * wave + 0.1 * rng.uniform(-1, 1)))), 3)


def post(url: str, body: dict, context) -> int:
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, context=context, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as e:
        return e.code


def stream(url: str, context, equipment_id: str, site_id: str, sensors: List[Sensor], interval: float,
           count: Optional[int], send: Callable = post, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
           sleep: Callable[[float], None] = time.sleep, out=sys.stdout) -> int:
    """Send one reading per sensor every ``interval`` seconds, ``count`` rounds or until stopped.
    Returns how many readings the edge did not accept."""
    rng, refused, step = random.Random(0), 0, 0
    while count is None or step < count:
        stamp = clock().isoformat()
        statuses = []
        for sensor in sensors:
            body = readings.reading(timestamp=stamp, sensor_id=sensor.sensor_id, equipment_id=equipment_id,
                                    site_id=site_id, value=value(sensor, step, rng))
            status = send(url, body, context)
            statuses.append(f"{sensor.name}={body['value']} {status}")
            refused += status != 202
        print(f"{stamp} " + "  ".join(statuses), file=out, flush=True)
        step += 1
        if count is None or step < count:
            sleep(interval)
    return refused


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--url", required=True, help="https://<host>:8443/ingest")
    p.add_argument("--cert", required=True, help="the device's certificate chain (scripts/device-ca.sh issue)")
    p.add_argument("--key", required=True, help="the device's private key")
    p.add_argument("--ca", required=True, help="CA bundle the edge's certificate is verified against")
    p.add_argument("--equipment-id", required=True)
    p.add_argument("--site-id", required=True)
    p.add_argument("--sensor", required=True, action="append", type=parse_sensor,
                   help="<name>=<sensor-uuid>:<low>:<high>, once per sensor")
    p.add_argument("--interval", type=float, default=1.0, help="seconds between rounds")
    p.add_argument("--count", type=int, help="rounds to send; until stopped when omitted")
    a = p.parse_args(argv)
    context = readings.device_tls_context(a.cert, a.key, a.ca)
    try:
        refused = stream(a.url, context, a.equipment_id, a.site_id, a.sensor, a.interval, a.count)
    except KeyboardInterrupt:
        return 0
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main())
