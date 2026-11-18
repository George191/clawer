"""Coalesce bounded, process-local reads of shared monitoring infrastructure."""

import asyncio
from time import monotonic

from fastapi import HTTPException


class MonitorSnapshots:
    def __init__(self, ttl, concurrent, capacity):
        self.ttl = ttl
        self.concurrent = concurrent
        self.capacity = capacity
        self.values = {}
        self.pending = {}

    async def read(self, key, observe):
        cached = self.values.get(key)
        if cached is not None and monotonic() < cached[0]:
            return cached[1]
        if key not in self.pending:
            if len(self.pending) >= self.concurrent:
                raise HTTPException(503, "Monitoring probes are busy. Retry shortly.")

            async def collect():
                try:
                    value = await observe()
                    self.values.pop(key, None)
                    if len(self.values) >= self.capacity:
                        del self.values[next(iter(self.values))]
                    self.values[key] = (monotonic() + self.ttl, value)
                    return value
                finally:
                    self.pending.pop(key, None)

            self.pending[key] = asyncio.create_task(collect())
        return await asyncio.shield(self.pending[key])
