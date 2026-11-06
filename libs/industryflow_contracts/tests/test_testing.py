# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The tests' store empties only a test server's database: one that holds keys on a server not marked
as a test server is refused, never emptied. Needs a real server (CI gives one, IF_REQUIRE_REAL_REDIS)."""
import pytest

from industryflow_contracts import testing


@pytest.fixture
def server():
    url = testing.real_redis_url()
    if url is None:
        pytest.skip("the marker lives on a real server; the fake is new for every test")
    import redis
    marker = redis.Redis.from_url(url, db=testing.MARKER_DB)
    held = marker.exists(testing.MARKER_KEY)
    marker.delete(testing.MARKER_KEY)
    other = redis.Redis.from_url(url, db=2)
    other.flushdb()
    yield url.rsplit("/", 1)[0], other
    other.flushdb()
    if held:
        marker.set(testing.MARKER_KEY, "1")


def test_a_database_in_use_on_an_unmarked_server_is_refused_and_kept(server):
    base, other = server
    other.set("someone:else", "1")
    with pytest.raises(RuntimeError, match="not a test server"):
        testing._emptied(f"{base}/2")
    assert other.get("someone:else") == b"1"


def test_an_empty_database_marks_the_server_and_is_emptied_from_then_on(server):
    base, other = server
    testing._emptied(f"{base}/2")
    other.set("left:by-a-test", "1")
    testing._emptied(f"{base}/2")
    assert other.dbsize() == 0


def test_the_marker_database_is_never_a_test_database(server):
    base, _ = server
    with pytest.raises(RuntimeError, match="marker"):
        testing._emptied(f"{base}/{testing.MARKER_DB}")
