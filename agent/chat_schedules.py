"""Dispatch durable chat schedules into the existing agent run queue."""

import asyncio
import logging

from sqlalchemy import text

from agent.contracts import AgentRunRequest
from agent.store import AgentStore

logger = logging.getLogger(__name__)


async def dispatch_schedules(store: AgentStore):
    # Row locks fence concurrent Web processes. The due timestamp is also the
    # enqueue idempotency key, including recovery after an ambiguous DB response.
    async with store.database.begin() as conn:
        rows = (await conn.execute(text("""SELECT s.*,c.owner_id FROM app.scout_chat_schedules s
            JOIN app.scout_chats c ON c.id=s.chat_id
            JOIN app."user" u ON u.id=c.owner_id
            WHERE s.enabled AND NOT c.archived AND u.status=0 AND s.next_run_at<=now()
            ORDER BY s.next_run_at LIMIT 10 FOR UPDATE OF s SKIP LOCKED"""))).mappings().all()
        for row in rows:
            run_id = None
            error = None
            try:
                run = await store.enqueue(row["owner_id"], f"scout:{row['chat_id']}:{row['next_run_at'].isoformat()}",
                                          AgentRunRequest.model_validate(row["request"]))
                run_id = run["id"]
            except Exception:
                error = "Scheduled run could not be queued. Check the agent configuration and queue."
                logger.exception("Chat schedule dispatch failed chat=%s", row["chat_id"])
            await conn.execute(text("""UPDATE app.scout_chat_schedules
                SET next_run_at=now()+make_interval(secs => interval_seconds),
                last_run_id=COALESCE(:run,last_run_id),last_error=:error WHERE chat_id=:id"""),
                {"id": row["chat_id"], "run": run_id, "error": error})


async def serve_schedules(store: AgentStore):
    while True:
        try:
            await dispatch_schedules(store)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Chat schedule dispatcher failed")
        await asyncio.sleep(30)
