# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The tracking store is recorded only once nothing of the tenant remains in it (ADR-0033 dec 4)."""
import io
import json

import pytest

from tenant_delete.tracking_store import TrackingStore

A = "0b2f503a-6e59-4f4f-b0cd-b82547a20cf2"


def _gateway(answer):
    sent = []

    def opener(request, timeout):
        sent.append(request)
        return io.BytesIO(json.dumps(answer).encode())
    return opener, sent


def test_the_call_is_the_operator_delete_with_the_secret():
    opener, sent = _gateway({"company_id": A, "remaining": {"experiments": 0, "registered_models": 0, "artifacts": 0}})
    removal = TrackingStore("http://gw:5050/", "s3cret", opener=opener).purge(A.upper(), [])
    [request] = sent
    assert request.get_method() == "DELETE"
    assert request.full_url == f"http://gw:5050/api/2.0/industryflow-operator/tenants/{A}"
    assert request.get_header("Authorization") == "Bearer s3cret"
    assert removal.detail == {"remaining": {"experiments": 0, "registered_models": 0, "artifacts": 0}}


def test_anything_remaining_keeps_the_store_unrecorded():
    opener, _ = _gateway({"company_id": A, "remaining": {"experiments": 2, "registered_models": 0, "artifacts": 0}})
    assert TrackingStore("http://gw", "t", opener=opener).purge(A, []) is None


def test_an_answer_for_another_tenant_is_an_error():
    opener, _ = _gateway({"company_id": "11111111-2222-3333-4444-555555555555", "remaining": {}})
    with pytest.raises(RuntimeError):
        TrackingStore("http://gw", "t", opener=opener).purge(A, [])


def test_a_tenant_id_must_be_a_tenant_id():
    opener, sent = _gateway({})
    with pytest.raises(ValueError):
        TrackingStore("http://gw", "t", opener=opener).purge("../x", [])
    assert sent == []
