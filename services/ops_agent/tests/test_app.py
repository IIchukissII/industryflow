# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The receiver: authenticated by the shared secret, once per firing, never for a resolved alert."""

import asyncio

from fastapi.testclient import TestClient

import app as service
from test_graph import ALERT, FakePublisher, FakeTools, FakeWriter


def _client(config, publisher):
    return TestClient(service.create_app(config(), tools=FakeTools(), writer=FakeWriter(), publisher=publisher))


def _post(client, payload, secret="s" * 40):
    return client.post("/alertmanager", json=payload, headers={"Authorization": f"Bearer {secret}"})


def _settle(client):
    client.portal.call(asyncio.sleep, 0.2)


def test_the_receiver_refuses_without_the_secret(config):
    with _client(config, FakePublisher()) as client:
        assert client.post("/alertmanager", json={"alerts": [ALERT]}).status_code == 401
        assert _post(client, {"alerts": [ALERT]}, secret="wrong").status_code == 401


def test_a_firing_alert_is_explained_once(config):
    publisher = FakePublisher()
    with _client(config, publisher) as client:
        assert _post(client, {"alerts": [ALERT]}).json() == {"accepted": 1, "skipped": 0}
        assert _post(client, {"alerts": [ALERT]}).json() == {"accepted": 0, "skipped": 0}
        _settle(client)
    assert len(publisher.notes) == 1


def test_a_resolved_alert_is_not_explained(config):
    publisher = FakePublisher()
    with _client(config, publisher) as client:
        assert _post(client, {"alerts": [{**ALERT, "status": "resolved"}]}).json() == {"accepted": 0, "skipped": 0}
        _settle(client)
    assert publisher.notes == []


def test_the_agent_refuses_to_start_without_a_strong_secret(config):
    assert config(webhook_secret="short").refusals()
    assert config(model_provider="openai-compatible").refusals() == [
        "OPS_AGENT_MODEL_BASE_URL is required for an openai-compatible provider"]
    assert config().refusals() == []
    assert config(anthropic_credentials=False).refusals() == [
        "ANTHROPIC_API_KEY is unset, so the anthropic provider has no credential"]
    # A model server on the operator's host needs no key.
    assert config(model_provider="openai-compatible", model_base_url="http://gpu:8000/v1",
                  anthropic_credentials=False).refusals() == []


class RememberingPublisher(FakePublisher):
    async def published(self, key):
        return key == "f1@2026-10-31T10:00:00Z"


def test_a_storm_is_bounded_critical_first(config):
    publisher = FakePublisher()
    storm = [{**ALERT, "fingerprint": f"f{i}", "labels": {**ALERT["labels"], "severity": "warning"}} for i in range(30)]
    storm.append({**ALERT, "fingerprint": "crit", "labels": {**ALERT["labels"], "severity": "critical"}})
    with _client(config, publisher) as client:
        assert _post(client, {"alerts": storm}).json() == {"accepted": service.MAX_PER_GROUP,
                                                          "skipped": 31 - service.MAX_PER_GROUP}
        _settle(client)
    assert len(publisher.notes) == service.MAX_PER_GROUP
    assert "crit" in {n["fingerprint"] for n in publisher.notes}


def test_a_firing_already_explained_before_a_restart_is_not_explained_again(config):
    publisher = RememberingPublisher()
    with _client(config, publisher) as client:
        assert _post(client, {"alerts": [ALERT]}).json()["accepted"] == 1
        _settle(client)
    assert publisher.notes == []


def test_a_repeat_after_the_routers_repeat_interval_is_still_the_same_firing():
    assert service.SEEN_TTL_SECONDS > 4 * 3600
