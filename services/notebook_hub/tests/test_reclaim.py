# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The reclaim records a tenant's volumes only once every one of its users is gone from the hub."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import reclaim  # noqa: E402

A, B = "0b2f503a-6e59-4f4f-b0cd-b82547a20cf2", "11111111-2222-3333-4444-555555555555"


def _run(pending, outcomes):
    deleted, recorded = [], []

    def delete_user(user_id):
        deleted.append(user_id)
        outcome = outcomes.get(user_id, reclaim.DELETED)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    done = reclaim.reclaim_once(pending, delete_user, lambda c, d: recorded.append((c, d)))
    return deleted, recorded, done


def test_every_user_is_deleted_then_the_store_recorded():
    deleted, recorded, _ = _run([(A, ["u1", "u2", "u3"])], {"u3": reclaim.ABSENT})
    assert deleted == ["u1", "u2", "u3"]
    assert recorded == [(A, {"hub_users_deleted": 2, "users_never_in_hub": 1})]


def test_a_tenant_without_users_is_recorded_as_nothing_to_reclaim():
    _, recorded, _ = _run([(A, [])], {})
    assert recorded == [(A, {"hub_users_deleted": 0, "users_never_in_hub": 0})]


def test_a_user_still_stopping_or_failing_leaves_only_its_tenant_for_the_next_pass():
    _, recorded, done = _run([(A, ["u1", "u2"]), (B, ["u3"])],
                             {"u1": reclaim.BUSY, "u2": RuntimeError("docker unreachable")})
    assert [c for c, _ in recorded] == [B] and list(done) == [B]
