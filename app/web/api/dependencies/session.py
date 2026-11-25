from typing import Annotated
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from collections.abc import  AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import DBAPIError
from fastapi import Depends, HTTPException

from app.web.core.db import engine
from app.storage.business_database import BusinessDatabaseNotConfigured, get_business_pg_client


async def get_db() -> AsyncGenerator[AsyncSession, None, None]:
    async with AsyncSession(engine) as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_db)]


@asynccontextmanager
async def _business_db() -> AsyncGenerator[AsyncSession, None]:
    pg = get_business_pg_client()
    try:
        await pg.connect(max_retries=1)
        async with pg.session() as session:
            yield session
    except BusinessDatabaseNotConfigured as error:
        raise HTTPException(409, str(error)) from None
    except (ConnectionError, OSError, TimeoutError, DBAPIError):
        raise HTTPException(503, "Database access is unavailable; verify the system registry and selected business connection") from None


def get_business_db() -> AbstractAsyncContextManager[AsyncSession]:
    return _business_db()
