# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Pure helper for the streaming upsert's FK resilience (ADR-0006).

sensor_measurements has a FK to sensors(sensor_id). A measurement whose sensor_id is not
provisioned (an orphan — e.g. a deleted sensor or a stray test message) raises a
ForeignKeyViolation, which aborts the whole micro-batch transaction; the stream then retries
the same batch forever, wedging the pipeline for that tenant. Filtering orphan rows out before
the insert lets the valid rows commit and keeps the pipeline flowing.

Kept dependency-free (no pyspark/psycopg2) so it can be shipped to executors via --py-files and
unit-tested on its own.
"""
from __future__ import annotations

# Index of sensor_id within a measurement row tuple (see MEASUREMENT_COLUMNS in
# kafka_to_timescaledb.py: time, sensor_id, equipment_id, site_id, value, unit, quality_code).
SENSOR_ID_INDEX = 1


def split_known_sensors(rows, valid_sensor_ids, sensor_id_index: int = SENSOR_ID_INDEX):
    """Partition rows into (kept, skipped_count) by whether their sensor_id is provisioned.

    valid_sensor_ids is the set of sensor_ids present in the tenant's sensors table. Comparison
    is on the string form so UUID objects and strings match regardless of source type.
    """
    valid = {str(s) for s in valid_sensor_ids}
    kept = []
    skipped = 0
    for row in rows:
        if str(row[sensor_id_index]) in valid:
            kept.append(row)
        else:
            skipped += 1
    return kept, skipped
