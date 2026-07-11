# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Entrypoint for the cold-layer export job (ADR-0025).

One linear task — export -> verify -> drop over every tenant, run on a schedule by a Kubernetes
CronJob (decision 9). This module only wires the environment to the real adapters and hands off
to `run_export`; all the ordering and idempotency logic lives in `exporter.py`. A non-zero exit
on failure lets the CronJob mark the run failed and retry (backoffLimit).
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone

from .config import load_config
from .exporter import run_export
from .source import PostgresMeasurementSource
from .store import S3ColdStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("cold_export")


def main() -> int:
    cfg = load_config()
    source = PostgresMeasurementSource(cfg.db)
    store = S3ColdStore(cfg.store)
    # The horizon is measured against the UTC calendar day at run time.
    today = datetime.now(timezone.utc).date()
    try:
        run_export(source, store, horizon_days=cfg.horizon_days, today=today)
    except Exception:  # noqa: BLE001 — top-level: log and fail the job so the CronJob retries
        logger.exception("cold export run failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
