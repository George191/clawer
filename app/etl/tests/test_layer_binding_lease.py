"""Worker batches pin all participating layer connections through offset commit."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch

from aiokafka import TopicPartition
from fastapi import HTTPException

from app.etl import base, ts_ods
from app.storage import business_database as routing
from app.web.services.data_connections import DataConnections


class LayerBindingLeaseTests(IsolatedAsyncioTestCase):
    async def test_changed_source_aborts_before_processing(self):
        rows = [{"layer": "ods", "id": "target", "updated_at": 1},
                {"layer": "rds", "id": "new-source", "updated_at": 2}]
        result = Mock()
        result.mappings.return_value = rows
        execute = AsyncMock(side_effect=[None, result])

        @asynccontextmanager
        async def session():
            yield SimpleNamespace(execute=execute)

        previous = {"ods": ("target", 1), "rds": ("old-source", 2)}
        with self.assertRaises(routing.LayerBindingChanged):
            async with routing.layer_binding_lease(("rds", "ods"), expected=previous,
                                                   pg=SimpleNamespace(session=session)):
                self.fail("Changed source reached business processing")
        lock, read = execute.await_args_list
        self.assertIn("pg_advisory_xact_lock_shared", str(lock.args[0]))
        self.assertEqual(lock.args[1], {"layers": ["ods", "rds"]})
        self.assertIn("etl_layer_connections", str(read.args[0]))

    async def test_ods_holds_source_and_target_until_offset_acknowledgement(self):
        worker = ts_ods.TsOds()
        worker._running = True
        tp = TopicPartition("records", 0)
        message = SimpleNamespace(offset=27)
        worker._consumer = Mock()
        worker._consumer.assignment.return_value = {tp}
        worker._consumer.getmany = AsyncMock(return_value={tp: [message]})
        events = []

        @asynccontextmanager
        async def lease(layers, **_):
            self.assertEqual(set(layers), {"rds", "ods"})
            events.append("lock")
            yield {}
            events.append("unlock")

        async def write(*_):
            events.append("write")
            return True

        async def acknowledge(topic, partition, offset, *, owner):
            self.assertEqual((topic, partition, offset), ("records", 0, 27))
            events.append("ack")
            worker._running = False

        with (patch.object(base, "layer_binding_lease", lease),
              patch.object(worker, "_dispatch_batch", write),
              patch.object(worker, "_commit_and_save", acknowledge)):
            await worker._consume_loop()
        self.assertEqual(events, ["lock", "write", "ack", "unlock"])

    async def test_binding_api_rejects_busy_layer_before_replacing_it(self):
        service = DataConnections()
        session = SimpleNamespace(scalar=AsyncMock(return_value=False), execute=AsyncMock())

        @asynccontextmanager
        async def open_session():
            yield session

        with patch.object(service, "_session", open_session), self.assertRaises(HTTPException) as busy:
            await service.bind_layer("ods", None)
        self.assertEqual(busy.exception.status_code, 409)
        self.assertIn("pg_try_advisory_xact_lock", str(session.scalar.await_args.args[0]))
        self.assertEqual(session.scalar.await_args.args[1], {"name": "etl-layer:ods"})
        session.execute.assert_not_awaited()
