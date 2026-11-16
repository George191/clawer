import asyncio
import unittest

from app.engine.spider_engine import SpiderEngine
from app.utils.runtime_control import reset_control_checkpoint, set_control_checkpoint


class AdapterRequestControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_checkpoint_interrupts_adapter_without_response(self):
        started = asyncio.Event()
        closed = asyncio.Event()
        stop = False

        async def request():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()

        async def checkpoint():
            return not stop

        token = set_control_checkpoint(checkpoint)
        try:
            task = asyncio.create_task(SpiderEngine._controlled_adapter_call(request()))
            await started.wait()
            stop = True
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
            self.assertTrue(closed.is_set())
        finally:
            reset_control_checkpoint(token)
