# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Test config for the Spark jobs: the job directory on the path, and one local SparkSession.

The jobs' routing and windowing run on a real local Spark (pyspark from requirements-dev.txt, and a
JDK — CI's runner provides one). Kafka and TimescaleDB are not needed: the batch routing takes its
database lookups, dead-letter publisher and writer as arguments, and the streaming tests read from a
file source. test_spark_service.py is a live-stack harness (TimescaleDB + running jobs); CI runs this
directory with it --ignore'd.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
# Python workers must run the interpreter the driver runs (PySpark refuses a minor-version mismatch).
os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
os.environ.setdefault("PYSPARK_DRIVER_PYTHON", sys.executable)


@pytest.fixture(scope="session")
def spark():
    from pyspark.sql import SparkSession

    session = (
        SparkSession.builder.master("local[2]")
        .appName("spark-jobs-tests")
        .config("spark.sql.shuffle.partitions", "2")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.session.timeZone", "UTC")
        .getOrCreate()
    )
    session.sparkContext.setLogLevel("ERROR")
    yield session
    session.stop()
