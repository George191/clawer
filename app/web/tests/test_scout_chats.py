"""Chat access contracts plus opt-in PostgreSQL integration checks."""

import json
import sys
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from fastapi import HTTPException, Response
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.web.api.routes import scout_chats as api


def turn():
    return dict(id=1, prompt="Test request", reply="Test reply", template="", adapter="", step=5, done=6, complete=True)


def result(row):
    value = Mock()
    value.mappings.return_value.first.return_value = row
    return value


class ChatAccessTest(unittest.IsolatedAsyncioTestCase):
    def test_bounds_and_restricted_recipients(self):
        with self.assertRaises(ValidationError):
            api.SaveChat(title="Chat", turns=[{**turn(), "step": 8}], revision=0)
        with self.assertRaises(ValidationError):
            api.ShareChat(audience="invited", snapshot=api.SaveChat(title="Chat", turns=[turn()], revision=0))

    async def test_other_owner_chat_is_not_found(self):
        session = SimpleNamespace(execute=AsyncMock(return_value=result(None)))
        owner, chat_id = uuid4(), uuid4()
        with self.assertRaises(HTTPException) as failure:
            await api.owned(session, chat_id, owner)
        self.assertEqual(failure.exception.status_code, 404)
        self.assertEqual(session.execute.call_args.args[1], {"id": chat_id, "owner": owner})

    async def test_public_share_does_not_expose_access_metadata(self):
        row = dict(title="Chat", turns=[turn()], created_at=None, audience="anyone", emails=["private@example.test"], owner_id=uuid4())
        session = SimpleNamespace(execute=AsyncMock(return_value=result(row)))
        response = Response()
        shared = await api.read_share(uuid4(), session, response)
        self.assertEqual(set(shared), {"title", "turns", "created_at"})
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    async def test_restricted_share_requires_invited_identity(self):
        row = dict(title="Chat", turns=[turn()], created_at=None, audience="invited", emails=["invited@example.test"], owner_id=uuid4())
        session = SimpleNamespace(execute=AsyncMock(return_value=result(row)))
        with self.assertRaises(HTTPException) as failure:
            await api.read_share(uuid4(), session, Response())
        self.assertEqual(failure.exception.status_code, 401)
        with patch.object(api, "get_current_user", AsyncMock(return_value=SimpleNamespace(id=uuid4(), email="outsider@example.test"))):
            with self.assertRaises(HTTPException) as failure:
                await api.read_share(uuid4(), session, Response(), "token")
            self.assertEqual(failure.exception.status_code, 403)
        with patch.object(api, "get_current_user", AsyncMock(return_value=SimpleNamespace(id=uuid4(), email="INVITED@example.test"))):
            self.assertEqual((await api.read_share(uuid4(), session, Response(), "token"))["turns"], [turn()])

    async def test_stale_revision_does_not_commit(self):
        session = SimpleNamespace(execute=AsyncMock(return_value=result(None)), commit=AsyncMock())
        with self.assertRaises(HTTPException) as failure:
            await api.save_chat(uuid4(), api.SaveChat(title="Chat", turns=[turn()], revision=9), SimpleNamespace(id=uuid4()), session)
        self.assertEqual(failure.exception.status_code, 409)
        session.commit.assert_not_awaited()

    async def test_schedule_dispatch_records_success_and_failure(self):
        from agent.chat_schedules import dispatch_schedules

        owner, first, second, run_id = uuid4(), uuid4(), uuid4(), uuid4()
        due = datetime.now(timezone.utc)
        request = {"agent_id": str(uuid4()), "product_domain": "ai-collect", "input": {"task": "Check public evidence"}}
        rows = [{"chat_id": ident, "owner_id": owner, "next_run_at": due, "request": request} for ident in [first, second]]
        returned = Mock()
        returned.mappings.return_value.all.return_value = rows
        conn = SimpleNamespace(execute=AsyncMock(return_value=returned))
        transaction = Mock()
        transaction.__aenter__ = AsyncMock(return_value=conn)
        transaction.__aexit__ = AsyncMock(return_value=False)
        store = SimpleNamespace(database=SimpleNamespace(begin=Mock(return_value=transaction)),
                                enqueue=AsyncMock(side_effect=[{"id": run_id}, RuntimeError("queue unavailable")]))
        await dispatch_schedules(store)
        self.assertIn("FOR UPDATE OF s SKIP LOCKED", str(conn.execute.call_args_list[0].args[0]))
        self.assertEqual(store.enqueue.call_args_list[0].args[1], f"scout:{first}:{due.isoformat()}")
        self.assertEqual(conn.execute.call_args_list[1].args[1]["run"], run_id)
        self.assertIsNone(conn.execute.call_args_list[1].args[1]["error"])
        self.assertIn("could not be queued", conn.execute.call_args_list[2].args[1]["error"])


class DatabaseIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_crud_snapshot_and_branch_isolation(self):
        from app.web.core.db import engine

        ident, branch_id = uuid4(), None
        async with AsyncSession(engine) as session:
            owner = (await session.execute(text('SELECT id FROM app."user" LIMIT 1'))).scalar_one()
            user = SimpleNamespace(id=owner)
            try:
                saved = await api.save_chat(ident, api.SaveChat(title="Scout integration test", turns=[turn()], revision=0), user, session)
                self.assertEqual(saved["revision"], 1)
                await session.execute(text("""INSERT INTO app.scout_chat_schedules(chat_id,request,interval_seconds,next_run_at)
                    VALUES (:id,'{}',86400,now()+interval '1 day')"""), {"id": ident})
                await session.commit()
                payload = api.ShareChat(snapshot=api.SaveChat(title=saved["title"], turns=saved["turns"], revision=1))
                shared = await api.share_chat(ident, payload, user, session)
                branch = await api.branch_chat(ident, user, session)
                branch_id = branch["id"]
                self.assertNotEqual(branch_id, ident)
                await api.save_chat(ident, api.SaveChat(title="Renamed", turns=[{**turn(), "reply": "Changed"}], pinned=True, archived=True, revision=1), user, session)
                self.assertIsNone(await api.read_schedule(ident, user, session))
                self.assertEqual((await api.owned(session, branch_id, owner))["turns"][0]["reply"], "Test reply")
                self.assertEqual((await api.read_share(shared["id"], session, Response()))["turns"][0]["reply"], "Test reply")
                with self.assertRaises(HTTPException):
                    await api.owned(session, ident, uuid4())
                await api.delete_chat(ident, user, session)
                with self.assertRaises(HTTPException):
                    await api.read_share(shared["id"], session, Response())
            finally:
                await session.rollback()
                await session.execute(text("DELETE FROM app.scout_chats WHERE id IN (:id,:branch)"), {"id": ident, "branch": branch_id or ident})
                await session.commit()
        await engine.dispose()


if __name__ == "__main__":
    integration = "--integration" in sys.argv
    unittest.main(argv=[sys.argv[0]], defaultTest=["ChatAccessTest", "DatabaseIntegration"] if integration else "ChatAccessTest")
