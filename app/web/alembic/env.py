from logging.config import fileConfig
import asyncio
from pathlib import Path
import sys

from alembic import context
from pydantic import PostgresDsn
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import async_engine_from_config

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Alembic loads this module from the script directory.  Add the repository
# root explicitly so both `alembic -c alembic.ini` and the legacy web config
# resolve the same application package.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Interpret the config file for Python logging.
# This line sets up loggers basically.
fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
# target_metadata = None

from app.web.api.models import SQLModel
from app.web.core.config import settings # noqa

target_metadata = SQLModel.metadata

# System tables and the migration version are owned by app. Physical ts_*
# tables belong to the business database selected through Data Connections.
_USER_SCHEMAS = {"app"}


def include_name(name, type_, parent_names):
    # Ontology owns these table definitions and its explicit metadata migration.
    if type_ == "table" and parent_names.get("schema_name") in {None, "app"}:
        if name in {"ontology_objects", "ontology_object_properties", "ontology_object_relations",
                    "ontology_data_connections", "etl_business_connection"}:
            return False
    if type_ != "schema":
        return True
    if name is None:
        return True
    return name in _USER_SCHEMAS

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def get_url():
    dsn: PostgresDsn = settings.SQLALCHEMY_MIGRATE_DATABASE_URI
    return dsn.unicode_string()


def run_migrations_offline():
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        include_schemas=True,
        include_name=include_name,
        version_table_schema="app",
    )

    with context.begin_transaction():
        context.execute("CREATE SCHEMA IF NOT EXISTS app")
        context.execute("SET LOCAL search_path=app,pg_catalog")
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_schemas=True,
        include_name=include_name,
        version_table_schema="app",
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations():
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    configuration = config.get_section(config.config_ini_section)
    configuration["sqlalchemy.url"] = settings.SQLALCHEMY_RUNNABLE_DATABASE_URI.unicode_string()
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args={"server_settings": {"search_path": "app,pg_catalog"}},
    )

    async with connectable.connect() as connection:
        await connection.execute(text("CREATE SCHEMA IF NOT EXISTS app"))
        await connection.commit()
        connection.dialect.default_schema_name = "app"
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online():
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
