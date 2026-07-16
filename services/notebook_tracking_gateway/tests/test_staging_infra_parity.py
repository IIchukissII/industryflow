# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The staging root is known in two places, so something has to hold them together (ADR-0030).

The gateway decides where an unadmitted artifact rests. The object store's lifecycle rule decides
what eventually collects one nobody committed. Those are different systems and neither can read the
other, so the prefix is written twice — and a comment saying "must match" is not a mechanism.

If they drift, nothing fails: uploads keep working, staging keeps filling, and the expiry quietly
applies to a prefix nothing writes to. The bytes ADR-0030 said must not accumulate accumulate
forever, and the first symptom is a storage bill. A silent wrong answer, which is the failure mode
this project refuses everywhere else.

Mirrors `scripts/check_model_env_parity.py`: a value split across code and infrastructure gets a
check that reads both, rather than a promise that both were remembered.
"""
import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import staging  # noqa: E402

_COMPOSE = os.path.join(os.path.dirname(__file__), "..", "..", "..", "docker-compose.yml")


def _minio_init_env():
    if not os.path.exists(_COMPOSE):
        pytest.skip("docker-compose.yml not present in this checkout")
    with open(_COMPOSE) as fh:
        compose = yaml.safe_load(fh)
    return compose["services"]["minio-init"]["environment"]


def _default_of(value: str) -> str:
    """`${NAME:-default}` -> `default`."""
    return value.split(":-", 1)[1].rstrip("}") if ":-" in value else value


def test_the_expiry_rule_targets_the_prefix_the_gateway_actually_stages_into():
    env = _minio_init_env()
    assert _default_of(str(env["UPLOAD_STAGING_PREFIX"])) == staging.STAGING_ROOT


def test_abandoned_uploads_actually_expire():
    # An upload staged and never committed is a certainty, not an edge case — a closed laptop is
    # enough — and nothing in the request path returns to collect it.
    env = _minio_init_env()
    assert int(_default_of(str(env["UPLOAD_STAGING_EXPIRE_DAYS"]))) >= 1


def test_staging_shares_the_artifact_bucket_and_is_kept_out_by_prefix_not_by_bucket():
    """Worth pinning: the isolation comes from the staging root sitting outside every tenant prefix,
    not from a separate bucket. A future change that moved staging into a tenant's prefix would keep
    every test green except the ones that say why it must not."""
    env = _minio_init_env()
    assert _default_of(str(env["UPLOAD_STAGING_BUCKET"])) == "mlflow"
    assert not staging.STAGING_ROOT.startswith("tenant_")
