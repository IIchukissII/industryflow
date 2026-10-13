# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The alert worker's environment is a list written in three places, and an omission fails silently.

The worker read REDIS_URL, KAFKA_MAX_RETRIES, KAFKA_RETRY_BACKOFF_SECONDS and KAFKA_TOPIC_DLQ with
getattr(config, ..., default) from a config class that never defined them, so whatever a deployment
set was ignored and the defaults ran. Compose meanwhile never set REDIS_URL at all. Nothing failed.

So this reads both sides:
- every variable a deployment sets for the worker is one config.py actually reads (no dead settings);
- every variable the worker cannot start without is set by every deployment;
- config.py is the only place in the worker that reads the environment.
"""
import ast
import glob
import os
import re

import pytest
import yaml

_HERE = os.path.dirname(__file__)
_SERVICE = os.path.join(_HERE, "..")
_ROOT = os.path.join(_SERVICE, "..", "..")
_CONFIG = os.path.join(_SERVICE, "config.py")
_COMPOSE = os.path.join(_ROOT, "docker-compose.yml")
_CHART = os.path.join(_ROOT, "deploy", "helm", "industryflow")
_APP = "alert-service-detector"


def _env_reads(path):
    """Names read via os.getenv('X') / os.environ.get('X') / os.environ['X'] in one file."""
    tree = ast.parse(open(path).read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
            f = node.func
            if isinstance(f, ast.Attribute) and (
                (f.attr == "getenv" and isinstance(f.value, ast.Name) and f.value.id == "os")
                or (f.attr == "get" and isinstance(f.value, ast.Attribute) and f.value.attr == "environ")
            ):
                names.add(node.args[0].value)
        if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute)
                and node.value.attr == "environ" and isinstance(node.slice, ast.Constant)):
            names.add(node.slice.value)
    return names


def _required():
    tree = ast.parse(open(_CONFIG).read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "REQUIRED_ENV" for t in node.targets):
            return {elt.value for elt in node.value.elts}
    raise AssertionError("config.py defines no REQUIRED_ENV")


def _compose_env():
    if not os.path.exists(_COMPOSE):
        pytest.skip("docker-compose.yml not present in this checkout")
    with open(_COMPOSE) as fh:
        return set(yaml.safe_load(fh)["services"][_APP]["environment"])


def _chart():
    if not os.path.isdir(_CHART):
        pytest.skip("the chart is not present in this checkout")
    with open(os.path.join(_CHART, "values.yaml")) as fh:
        values = yaml.safe_load(fh)
    template = open(os.path.join(_CHART, "templates", "configmap.yaml")).read()
    # The ConfigMap is shared by every app (envFrom): its rendered keys plus values.config.
    configmap = set(re.findall(r"^\s{2}([A-Z][A-Z0-9_]*):", template, re.M)) | set(values["config"])
    return configmap, set(values["apps"][_APP]["secretKeys"])


def test_every_variable_compose_sets_for_the_worker_is_read_by_config():
    unread = _compose_env() - _env_reads(_CONFIG)
    assert not unread, f"compose sets {sorted(unread)} for {_APP} but config.py never reads them"


def test_every_secret_the_chart_gives_the_worker_is_read_by_config():
    _, secrets = _chart()
    unread = secrets - _env_reads(_CONFIG)
    assert not unread, f"the chart hands {_APP} secrets it never reads: {sorted(unread)}"


def test_compose_sets_every_required_variable():
    missing = _required() - _compose_env()
    assert not missing, f"compose does not set {sorted(missing)}; the worker refuses to start without them"


def test_the_chart_provides_every_required_variable():
    configmap, secrets = _chart()
    missing = _required() - configmap - secrets
    assert not missing, f"the chart does not provide {sorted(missing)}"


def test_required_variables_are_read_by_config():
    assert _required() <= _env_reads(_CONFIG)


def test_config_is_the_only_reader_of_the_environment():
    for path in glob.glob(os.path.join(_SERVICE, "worker", "*.py")):
        assert not _env_reads(path), f"{os.path.basename(path)} reads the environment; route it through config.py"
