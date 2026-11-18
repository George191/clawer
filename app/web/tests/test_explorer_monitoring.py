import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException

from app.web.services import cdc_runtime, etl_capture, etl_runtime
from app.web.services.monitor_snapshots import MonitorSnapshots


@pytest.mark.asyncio
async def test_simultaneous_reads_share_snapshot_and_reject_excess_distinct_probes():
    cache = MonitorSnapshots(ttl=10, concurrent=1, capacity=1)
    started, finish = asyncio.Event(), asyncio.Event()

    async def probe():
        started.set()
        await finish.wait()
        return object()

    first = asyncio.create_task(cache.read("same", probe))
    await started.wait()
    second = asyncio.create_task(cache.read("same", probe))
    with pytest.raises(HTTPException) as busy:
        await cache.read("other", probe)
    assert busy.value.status_code == 503
    first.cancel()
    finish.set()
    value = await second
    assert await cache.read("same", AsyncMock(side_effect=AssertionError)) is value
    assert not cache.pending


def test_change_requires_exact_database_and_table_and_returns_only_event_coordinates():
    source = dict(connector="postgresql", name="prefix", db="dw", schema="ts_ods", table="ods_x", lsn=0x100000001)
    payload = dict(source=source, op="u", after={"private": "content"})
    message = SimpleNamespace(topic="prefix.ts_ods.ods_x", partition=0, offset=5, value={"payload": payload})
    identity = {"prefix": "prefix", "database": "dw"}
    observed = etl_capture._observation(message, identity, "ts_ods", "ods_x")
    assert observed == {"topic": message.topic, "partition": 0, "offset": 5, "operation": "update",
                        "sourceLsn": "1/1", "eventTimeMs": None}
    for attr, value in [("db", "elsewhere"), ("table", "ods_y"), ("schema", "ts_rds"),
                        ("connector", "mongo"), ("name", "other"), ("lsn", False)]:
        with patch.dict(source, {attr: value}):
            assert etl_capture._observation(message, identity, "ts_ods", "ods_x") is None


@pytest.mark.asyncio
async def test_orbital_counterpart_visibility_checked_before_cached_reference():
    layers = Mock()
    layers.runtime_table = AsyncMock(side_effect=[{"schemaName": "ts_ods"}, HTTPException(404)])
    with (patch.object(etl_capture, "get_etl_layers", return_value=layers),
          patch.object(etl_capture, "_CAPTURE", MonitorSnapshots(30, 1, 1)),
          patch.object(etl_capture, "_ORBITAL") as references):
        result = await etl_capture.capture("ts_ods", "ods_orbital", include_hidden=False)
    assert result["state"] == "outside_capture" and "recordLink" not in result
    references.read.assert_not_called()
    assert layers.runtime_table.await_args.args == ("ts_rds", "rds_orbital")
    assert layers.runtime_table.await_args.kwargs == {"include_hidden": False}


@pytest.mark.asyncio
async def test_stream_reports_kafka_when_checkpoint_store_is_down_and_does_not_invent_commits():
    partitions = [dict(partition=0, beginningOffset=2, endOffset=15, committedOffset=12),
                  dict(partition=1, beginningOffset=4, endOffset=17, committedOffset=None)]
    with (patch.object(etl_runtime, "_kafka_positions", AsyncMock(return_value=partitions)),
          patch.object(etl_runtime, "_REDIS") as redis):
        redis.ensure_connected = AsyncMock(side_effect=ConnectionError)
        result = await etl_runtime._stream_positions("topic", "group")
    assert result["available"] and not result["resumeAvailable"]
    assert result["offsets"][0]["lag"] == 3
    assert result["offsets"][1]["lag"] is None and result["offsets"][1]["committedOffset"] is None
    assert result["offsets"][1]["source"] == "earliest retained"


@pytest.mark.asyncio
async def test_lag_observer_primes_topics_without_joining_or_fetching():
    from aiokafka import TopicPartition
    from aiokafka.structs import OffsetAndMetadata

    p, deleted = TopicPartition("captured", 0), TopicPartition("removed", 0)
    admin, observer = AsyncMock(), AsyncMock()
    observer.assign, observer.pause = Mock(), Mock()
    admin.describe_topics.return_value = [{"topic": p.topic, "partitions": [{"partition": 0}]}]
    observer.end_offsets.return_value = {p: 21}
    with (patch.object(cdc_runtime.settings, "kafka_brokers", "broker"),
          patch.object(cdc_runtime, "AIOKafkaAdminClient", return_value=admin),
          patch.object(cdc_runtime, "AIOKafkaConsumer", return_value=observer) as client,
          patch.object(cdc_runtime, "_consumer_offsets", AsyncMock(return_value={
              "group": {p: OffsetAndMetadata(6, ""), deleted: OffsetAndMetadata(1, "")},
          }))):
        result = await cdc_runtime._kafka()
    assert result.consumers[0].lag == 15
    assert client.call_args.kwargs["group_id"] is None
    observer.assign.assert_called_once_with({p})
    observer.pause.assert_called_once_with(p)
    observer.getmany.assert_not_called()
