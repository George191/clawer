import logging
import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config

from sqlalchemy.ext.asyncio import AsyncSession

from app.web.core.db import engine, init_db

from app.web.utils.logger import get_logger

logger = get_logger(__name__)


async def init() -> None:
    async with AsyncSession(engine) as session:
        await init_db(session)


async def main() -> None:
    await logger.info("Creating initial data")
    await init()
    await logger.info("Initial data created")


if __name__ == "__main__":
    command.upgrade(Config(str(Path(__file__).with_name("alembic.ini"))), "head")
    asyncio.run(main())
