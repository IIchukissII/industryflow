# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The capability store never evicts (ADR-0015 dec 8): it holds denials whose absence grants, so a key
dropped for memory fails open. Each packaging starts its own Redis, and each must say so; a missing
policy is not safe either way, since the image's default can change and a maxmemory without one
leaves the store unbounded in a container that is killed at its limit."""
import re

from _repo import CHART, ROOT, load_yaml


def _policy(args):
    joined = " ".join(str(a) for a in args)
    policy = re.search(r"--maxmemory-policy\s+(\S+)", joined)
    limit = re.search(r"--maxmemory\s+(\S+)", joined)
    return (policy.group(1) if policy else None), (limit.group(1) if limit else None)


def test_compose_runs_the_store_without_eviction_and_with_a_limit():
    command = load_yaml("docker-compose.yml")["services"]["redis"]["command"]
    policy, limit = _policy(command.split() if isinstance(command, str) else command)
    assert policy == "noeviction" and limit


def test_the_chart_runs_the_store_without_eviction_and_with_a_limit():
    template = (CHART / "templates" / "infra-redis.yaml").read_text()
    assert '"--maxmemory-policy", "noeviction"' in template
    assert '"--maxmemory", {{ .Values.infra.redis.maxmemory | quote }}' in template
    assert load_yaml("deploy/helm/industryflow/values.yaml")["infra"]["redis"]["maxmemory"]


def test_the_store_is_watched_against_its_limit():
    rules = (ROOT / "infrastructure" / "prometheus" / "rules" / "platform.yml").read_text()
    assert "alert: IndustryFlowRedisMemoryHigh" in rules
