# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Open-loop load generator for the device ingest edge (docs/operations/capacity.md).

Sends --rate readings/s for --duration s through the mTLS edge, one reading per request, round-robin
over the sensors in --sensor-map (a JSON object of name -> sensor UUID in the device's tenant). Each
reading is stamped --run-start plus its sequence number in microseconds, so (time, sensor_id) never
collides and landed rows can be counted exactly. Run one process per 125 readings/s: a busier process
lags its own event loop, which shows up as latency and a lower achieved rate. N processes take
--workers N, --worker 0..N-1 and the same --run-start; their sequences interleave.

Writes one CSV row per request (start, seq, status, ms) to --csv and prints a JSON summary. SIGTERM or
SIGINT stops sending, waits for requests in flight, then writes both; use a new --csv path per run.
"""
import argparse
import asyncio
import csv
import json
import signal
import ssl
import time
from datetime import datetime, timedelta

import httpx


async def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--url", required=True, help="https://<host>:8443/ingest")
    p.add_argument("--cert", required=True, help="device certificate chain (scripts/device-ca.sh)")
    p.add_argument("--key", required=True, help="device private key")
    p.add_argument("--ca", default="", help="CA bundle for the edge certificate; empty skips verification")
    p.add_argument("--sensor-map", required=True)
    p.add_argument("--equipment-id", required=True)
    p.add_argument("--site-id", required=True)
    p.add_argument("--rate", type=float, required=True, help="readings/s for this process")
    p.add_argument("--duration", type=float, required=True, help="seconds")
    p.add_argument("--connections", type=int, default=64)
    p.add_argument("--csv", required=True)
    p.add_argument("--run-start", required=True, help="ISO time with offset; readings are stamped from here")
    p.add_argument("--worker", type=int, default=0, help="this process's index (seq = i * workers + worker)")
    p.add_argument("--workers", type=int, default=1)
    a = p.parse_args()

    sensors = list(json.load(open(a.sensor_map)).values())
    base = datetime.fromisoformat(a.run_start)
    ctx = ssl.create_default_context(cafile=a.ca or None)
    if not a.ca:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    ctx.load_cert_chain(a.cert, a.key)
    limits = httpx.Limits(max_connections=a.connections, max_keepalive_connections=a.connections)
    rows = []
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, stop.set)
    loop.add_signal_handler(signal.SIGINT, stop.set)
    # Bounds requests in flight, so a slow server shows up as latency rather than unbounded memory.
    sem = asyncio.Semaphore(a.connections * 4)

    async with httpx.AsyncClient(verify=ctx, limits=limits, timeout=10.0) as client:
        async def send(seq):
            body = {
                "timestamp": (base + timedelta(microseconds=seq)).isoformat(),
                "sensor_id": sensors[seq % len(sensors)],
                "equipment_id": a.equipment_id,
                "site_id": a.site_id,
                "value": float(seq % 1000) / 10.0,
                "unit": "u",
                "quality_code": 0,
            }
            t0 = time.monotonic()
            try:
                r = await client.post(a.url, json=body)
                status = r.status_code
            except Exception as e:  # noqa: BLE001 - a failed request is a measured outcome
                status = type(e).__name__
            rows.append((t0, seq, status, (time.monotonic() - t0) * 1000))
            sem.release()

        start = time.monotonic()
        tasks = []
        for i in range(int(a.rate * a.duration)):
            seq = i * a.workers + a.worker
            delay = start + i / a.rate - time.monotonic()
            if delay > 0:
                try:
                    await asyncio.wait_for(stop.wait(), delay)
                except asyncio.TimeoutError:
                    pass
            if stop.is_set():
                break
            await sem.acquire()
            tasks.append(asyncio.create_task(send(seq)))
        await asyncio.gather(*tasks)
        elapsed = time.monotonic() - start

    with open(a.csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t", "seq", "status", "ms"])
        w.writerows(rows)
    ok = sorted(r[3] for r in rows if r[2] in (200, 202))
    print(json.dumps({
        "rate": a.rate, "sent": len(rows), "ok": len(ok), "errors": len(rows) - len(ok),
        "achieved_per_s": round(len(rows) / elapsed, 1),
        "p50_ms": round(ok[len(ok) // 2], 1) if ok else None,
        "p95_ms": round(ok[int(len(ok) * 0.95)], 1) if ok else None,
        "error_kinds": sorted({str(r[2]) for r in rows if r[2] not in (200, 202)}),
    }))


asyncio.run(main())
