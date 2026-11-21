# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A latest reading is keyed by tenant and sensor: two tenants that chose one sensor id each keep theirs."""

import asyncio

import fakeredis

from messaging.redis_client import RedisClient

A = "0b2f503a-1c2d-4e5f-8a9b-0c1d2e3f4a5b"
B = "11111111-2222-4333-8444-555555555555"
SENSOR = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


def test_two_tenants_with_one_sensor_id_each_read_their_own_value():
    async def run():
        client = RedisClient()
        client.redis = fakeredis.FakeAsyncRedis(decode_responses=True)
        await client.set_sensor_value(A, SENSOR, 1.0, {"timestamp": "t"})
        await client.set_sensor_value(B, SENSOR, 2.0, {"timestamp": "t"})
        return await client.get_all_sensors(A), await client.get_all_sensors(B)

    a, b = asyncio.run(run())
    assert a == {SENSOR: {**a[SENSOR], "value": 1.0, "company_id": A}}
    assert b == {SENSOR: {**b[SENSOR], "value": 2.0, "company_id": B}}
