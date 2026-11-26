"""Fixed-stage harness with fencing, cancellation, restart recovery and explicit failure."""

from __future__ import annotations

import asyncio
import logging

from fastapi import HTTPException

from agent.contracts import HEARTBEAT_SECONDS, STAGES, CollectionRequest, LeaseLost
from agent.model import CollectionModel, ModelFailure
from agent.store import CollectionStore
from agent.tools import CollectionTools

logger = logging.getLogger(__name__)
ACTIVITIES = {"capture": "Searching", "template": "Working", "adapter": "Working", "validate": "Working"}


class CollectionHarness:
    def __init__(self, store: CollectionStore, tools_factory=CollectionTools):
        self.store = store
        self.tools_factory = tools_factory

    async def execute(self, run: dict) -> None:
        snapshot = run["model_config"]
        if snapshot.get("agent_id"):
            from uuid import UUID

            from agent.registry import AgentRegistry
            config, key, current = await AgentRegistry(self.store.database).resolve(run["owner_id"], UUID(snapshot["agent_id"]))
            if current != snapshot:
                raise ValueError("Agent configuration changed after this run was queued")
        else:
            config, key = await self.store.runtime_settings(snapshot["revision"])
        tools = self.tools_factory(CollectionModel(config, key, snapshot.get("instructions", "")))
        request = CollectionRequest.model_validate(run["request"])
        for name in STAGES[run["stage"]:]:
            await self.store.progress(run, name, ACTIVITIES[name])
            value = await asyncio.wait_for(getattr(tools, name)(request, run["checkpoints"]), timeout=tools.timeout(name))
            await self.store.checkpoint(run, name, value)
        await self.store.finish(run, "completed", result=run["checkpoints"]["validate"])

    async def guard(self, run: dict) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            if not await self.store.heartbeat(run["id"], run["lease_token"]):
                raise LeaseLost()

    async def process(self, run: dict) -> None:
        execution = asyncio.create_task(self.execute(run))
        guard = asyncio.create_task(self.guard(run))
        try:
            done, _ = await asyncio.wait({execution, guard}, return_when=asyncio.FIRST_COMPLETED)
            # Prefer lease failure if both finished during a cancellation/ownership race.
            if guard in done:
                await guard
            await execution
        except LeaseLost:
            logger.info("Collection lease revoked run=%s", run["id"])
        except asyncio.CancelledError:
            execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)
            try:
                await self.store.finish(run, "queued")
            except Exception:
                logger.warning("Shutdown checkpoint unavailable; lease recovery required run=%s", run["id"])
            raise
        except Exception as exc:
            code = (exc.code if isinstance(exc, ModelFailure) else
                    f"DEPENDENCY_HTTP_{exc.status_code}" if isinstance(exc, HTTPException) else
                    "STAGE_TIMEOUT" if isinstance(exc, asyncio.TimeoutError) else
                    "OUTPUT_CONTRACT_INVALID" if isinstance(exc, ValueError) else "EXECUTION_FAILED")
            logger.warning("Collection failed run=%s stage=%s code=%s", run["id"], run["stage"], code)
            await self.store.finish(run, "failed", error=code)
        finally:
            execution.cancel()
            guard.cancel()
            await asyncio.gather(execution, guard, return_exceptions=True)

    async def serve(self) -> None:
        while True:
            try:
                run = await self.store.claim()
                if run is None:
                    await asyncio.sleep(1)
                else:
                    await self.process(run)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Infrastructure errors are never converted into a successful run.
                # The lease remains recoverable if persistence itself is unavailable.
                logger.exception("Collection queue unavailable")
                await asyncio.sleep(3)
