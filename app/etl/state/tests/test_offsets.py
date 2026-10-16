"""Offset persistence contracts remain stable when ETL packages are reorganized."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import pytest

from app.etl.state import offsets


@pytest.fixture
def offset_store(monkeypatch):
    redis = SimpleNamespace(
        set=AsyncMock(),
        scan=AsyncMock(return_value=(0, [])),
        get=AsyncMock(),
        delete=AsyncMock(),
    )
    connection = SimpleNamespace(
        ensure_connected=AsyncMock(return_value=redis),
        mark_unavailable=Mock(),
        close=AsyncMock(),
    )
    monkeypatch.setattr(offsets, "RedisClient", lambda *args, **kwargs: connection)
    return offsets.OffsetManager(), redis, connection


@pytest.mark.asyncio
async def test_saved_positions_preserve_consumer_namespace_and_expiry(offset_store):
    manager, redis, _ = offset_store

    await manager.save_offset("etl-rds", "spider-crawler", 2, 143)
    assert await manager.set_offset("etl-ods", "spider-rds-processed", 0, 0) is True

    assert redis.set.await_args_list == [
        call("offset:etl-rds:spider-crawler:2", "143", ex=2592000),
        call("offset:etl-ods:spider-rds-processed:0", "0", ex=2592000),
    ]


@pytest.mark.asyncio
async def test_loading_reads_every_scan_page_and_skips_invalid_positions(offset_store):
    manager, redis, _ = offset_store
    redis.scan.side_effect = [
        (11, ["offset:etl-rds:topic:0", "offset:etl-rds:topic:bad"]),
        (0, ["offset:etl-rds:topic:1", "offset:etl-rds:topic:2", "offset:etl-rds:topic:3"]),
    ]
    values = {
        "offset:etl-rds:topic:0": "143",
        "offset:etl-rds:topic:1": "0",
        "offset:etl-rds:topic:2": "invalid",
        "offset:etl-rds:topic:3": None,
    }
    redis.get.side_effect = values.__getitem__

    assert await manager.load_offsets("etl-rds", "topic") == {0: 143, 1: 0}

    assert redis.scan.await_args_list == [
        call(cursor=0, match="offset:etl-rds:topic:*", count=50),
        call(cursor=11, match="offset:etl-rds:topic:*", count=50),
    ]


@pytest.mark.asyncio
async def test_reset_removes_every_page_only_for_the_requested_consumer(offset_store):
    manager, redis, _ = offset_store
    redis.scan.side_effect = [
        (11, ["offset:etl-rds:topic:0", "offset:etl-rds:topic:1"]),
        (23, []),
        (0, ["offset:etl-rds:topic:2"]),
    ]
    redis.delete.side_effect = [2, 1]

    assert await manager.reset_offsets("etl-rds", "topic") == 3

    assert redis.delete.await_args_list == [
        call("offset:etl-rds:topic:0", "offset:etl-rds:topic:1"),
        call("offset:etl-rds:topic:2"),
    ]
    assert redis.scan.await_args_list == [
        call(cursor=0, match="offset:etl-rds:topic:*", count=50),
        call(cursor=11, match="offset:etl-rds:topic:*", count=50),
        call(cursor=23, match="offset:etl-rds:topic:*", count=50),
    ]


@pytest.mark.parametrize(
    ("method", "extra_args", "fallback"),
    [
        ("save_offset", (0, 143), None),
        ("set_offset", (0, 143), False),
        ("load_offsets", (), {}),
        ("reset_offsets", (), 0),
    ],
)
@pytest.mark.asyncio
async def test_unavailable_redis_does_not_interrupt_consumption(
    offset_store, method, extra_args, fallback
):
    manager, redis, connection = offset_store
    connection.ensure_connected.return_value = None

    assert await getattr(manager, method)("etl-rds", "topic", *extra_args) == fallback

    redis.set.assert_not_awaited()
    redis.scan.assert_not_awaited()
    connection.mark_unavailable.assert_not_called()


@pytest.mark.parametrize(
    ("method", "redis_method", "extra_args", "fallback"),
    [
        ("save_offset", "set", (0, 143), None),
        ("set_offset", "set", (0, 143), False),
        ("load_offsets", "scan", (), {}),
        ("reset_offsets", "scan", (), 0),
    ],
)
@pytest.mark.asyncio
async def test_failed_redis_operation_allows_reconnection(
    offset_store, method, redis_method, extra_args, fallback
):
    manager, redis, connection = offset_store
    getattr(redis, redis_method).side_effect = ConnectionError("redis disconnected")

    assert await getattr(manager, method)("etl-rds", "topic", *extra_args) == fallback

    connection.mark_unavailable.assert_called_once_with()


@pytest.mark.asyncio
async def test_concurrent_partition_reads_share_serialized_connection_attempts(offset_store):
    manager, redis, connection = offset_store
    active = 0
    peak = 0

    async def connect():
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        return redis

    connection.ensure_connected.side_effect = connect

    results = await asyncio.gather(
        *(manager.load_offsets("etl-rds", f"topic-{index}") for index in range(5))
    )

    assert results == [{}] * 5
    assert connection.ensure_connected.await_count == 5
    assert peak == 1
