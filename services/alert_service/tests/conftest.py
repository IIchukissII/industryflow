# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The `integration` marker: tests that need a real Kafka broker (test_kafka_consumer_integration.py),
run by the unit-test workflow's kafka job and left out of its no-broker matrix."""


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: needs a real Kafka broker; run by the alert-kafka job in unit-tests.yml.",
    )
