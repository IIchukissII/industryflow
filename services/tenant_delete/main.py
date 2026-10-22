# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Entry point: one pass over every requested tenant deletion (ADR-0033 dec 4).

Exits non-zero when a deletion failed, so the scheduler retries; a deletion only waiting on a store
this deployment cannot yet empty is not a failure.
"""
from __future__ import annotations

import logging
import sys

import boto3
import redis

from .capability_store import CapabilityStore
from .cold_store import ColdStore
from .config import load_config
from .job import broker_step, run
from .registry import Registry

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("tenant_delete")


def main() -> int:
    try:
        cfg = load_config()
        store = CapabilityStore(redis.Redis.from_url(cfg.redis_url))
        steps = {
            "capability_store": store.purge,
            "broker": broker_step(cfg.broker_retention),
        }
        if cfg.cold is not None:
            client = boto3.client("s3", endpoint_url=cfg.cold.endpoint, region_name=cfg.cold.region,
                                  aws_access_key_id=cfg.cold.access_key,
                                  aws_secret_access_key=cfg.cold.secret_key)
            steps["cold_layer"] = ColdStore(client, cfg.cold.bucket).purge
        outcome = run(Registry(cfg.db.dsn_kwargs()), steps, cfg.backup_retention)
    except Exception:  # noqa: BLE001 - any failure is a failed run
        logger.exception("tenant-delete run failed")
        return 1
    if outcome is None:
        return 0
    logger.info("tenant-delete run: completed %s, waiting %s, failed %s",
                outcome.completed, outcome.waiting, list(outcome.failed))
    return 1 if outcome.failed else 0


if __name__ == "__main__":
    sys.exit(main())
