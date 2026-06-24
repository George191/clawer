from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.etl.base import ETLBase


class _TestWorker(ETLBase):
    _layer = "ods"


@pytest.mark.asyncio
async def test_execute_with_table_recovery_retries_after_missing_partition() -> None:
    worker = _TestWorker()
    worker._recover_registered_table = AsyncMock()

    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError('no partition of relation "ods_news" found for row')
        return "ok"

    result = await worker._execute_with_table_recovery("news", operation)

    assert result == "ok"
    assert attempts == 2
    worker._recover_registered_table.assert_awaited_once_with("news", "current")


@pytest.mark.asyncio
async def test_execute_with_table_recovery_retries_after_missing_table() -> None:
    worker = _TestWorker()
    worker._recover_registered_table = AsyncMock()

    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError('relation "ts_ods.ods_news" does not exist')
        return "ok"

    result = await worker._execute_with_table_recovery("news", operation)

    assert result == "ok"
    assert attempts == 2
    worker._recover_registered_table.assert_awaited_once_with("news", "current")


@pytest.mark.asyncio
async def test_execute_with_table_recovery_does_not_retry_unrelated_errors() -> None:
    worker = _TestWorker()
    worker._recover_registered_table = AsyncMock()

    async def operation() -> str:
        raise RuntimeError("duplicate key value violates unique constraint")

    with pytest.raises(RuntimeError, match="duplicate key"):
        await worker._execute_with_table_recovery("news", operation)

    worker._recover_registered_table.assert_not_awaited()
