"""Explorer queries run until completion or client cancellation."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from anyio import fail_after
from fastapi import HTTPException

from app.web.api.models.etl import PreviewRowsQuery
from app.web.api.routes.etl import table_preview_rows
from app.web.services.etl_layers import EtlLayers


@pytest.mark.asyncio
async def test_disconnect_cancels_query_and_waits_for_cleanup():
    started, cleaned = asyncio.Event(), asyncio.Event()

    async def data(*args, **kwargs):
        started.set()
        try:
            await asyncio.Future()
        finally:
            await asyncio.sleep(0)
            cleaned.set()

    async def receive():
        await started.wait()
        return {"type": "http.disconnect"}

    with patch("app.web.api.routes.etl.get_etl_layers", return_value=SimpleNamespace(data=data)):
        with pytest.raises(HTTPException) as cancelled:
            await table_preview_rows("ts_ods", "items", PreviewRowsQuery(),
                                     SimpleNamespace(receive=receive), SimpleNamespace())
    assert cancelled.value.status_code == 499
    assert cleaned.is_set()


@pytest.mark.asyncio
async def test_completed_query_releases_disconnect_watcher():
    released = asyncio.Event()

    async def receive():
        try:
            await asyncio.Future()
        finally:
            released.set()

    async def data(*args, **kwargs):
        await asyncio.sleep(0)
        return {"rows": [{"id": 1}]}

    with patch("app.web.api.routes.etl.get_etl_layers", return_value=SimpleNamespace(data=data)):
        result = await table_preview_rows("ts_ods", "items", PreviewRowsQuery(),
                                         SimpleNamespace(receive=receive), SimpleNamespace())
    assert result == {"rows": [{"id": 1}]}
    assert released.is_set()


@pytest.mark.asyncio
async def test_explorer_session_disables_database_and_application_deadlines():
    session = SimpleNamespace(execute=AsyncMock())

    @asynccontextmanager
    async def pg_session():
        yield session

    layers = object.__new__(EtlLayers)
    layers._pg = SimpleNamespace(connect=AsyncMock(), session=pg_session)
    with patch("app.web.services.etl_layers.fail_after", wraps=fail_after) as deadline:
        async with layers.session(query_timeout=False):
            pass
    deadline.assert_called_once_with(None)
    assert str(session.execute.call_args.args[0]) == "SET LOCAL statement_timeout = 0"
