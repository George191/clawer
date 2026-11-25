"""Migration business writes must not follow the system database URL."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from app.storage import business_database
from outputs.sec_migration_20261104 import facts_direct_baseline as facts
from outputs.source_dedup_20261109 import pg_worker
from tools import migrate_download_paths as downloads


@pytest.mark.asyncio
@pytest.mark.parametrize('module', [pg_worker, facts])
async def test_direct_migration_uses_selected_business_credentials_and_tls(module):
    url = 'postgresql+asyncpg://migration:test-password@business.example.test/analytics'
    transport = object()
    connection = SimpleNamespace(execute=AsyncMock())
    provider = business_database if module is pg_worker else module
    with patch.object(provider, 'selected_business_connection', AsyncMock(return_value={'id': 'saved'})), \
         patch.object(provider, 'business_connection_options', return_value=(url, {'ssl': transport})), \
         patch('asyncpg.connect', AsyncMock(return_value=connection)) as connect:
        result = await (pg_worker.connect('resume-test') if module is pg_worker else facts.pg_connect(2))
    assert result is connection
    selected = make_url(connect.await_args.args[0])
    assert (selected.host, selected.database, selected.username) == ('business.example.test', 'analytics', 'migration')
    assert connect.await_args.kwargs['ssl'] is transport
    assert connect.await_args.kwargs['server_settings']['application_name'] == (
        'resume-test' if module is pg_worker else 'sec-facts-baseline-2')


@pytest.mark.asyncio
@pytest.mark.parametrize('module', [pg_worker, facts])
async def test_missing_selection_rejects_migration_before_connecting(module):
    provider = business_database if module is pg_worker else module
    with patch.object(provider, 'selected_business_connection', AsyncMock(
            side_effect=business_database.BusinessDatabaseNotConfigured('No business database selected'))), \
         patch('asyncpg.connect', AsyncMock()) as connect:
        with pytest.raises(business_database.BusinessDatabaseNotConfigured):
            await (pg_worker.connect() if module is pg_worker else facts.pg_connect(0))
    connect.assert_not_awaited()


@pytest.mark.asyncio
async def test_download_routes_only_use_system_metadata_connection():
    pg = SimpleNamespace(connect=AsyncMock(), close=AsyncMock())
    with patch.object(downloads, 'get_pg_client', return_value=pg), \
         patch.object(downloads, 'get_business_pg_client') as business, \
         patch.object(downloads.collection_route_store, 'list_by_data_type', AsyncMock(return_value=['route'])):
        assert await downloads.routes() == ['route']
    business.assert_not_called()
    pg.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_download_business_connection_preserves_transport_and_null_pool():
    transport = object()
    pg = SimpleNamespace(_engine=None, _connect_args={'ssl': transport}, resolve=AsyncMock())
    @asynccontextmanager
    async def context():
        yield SimpleNamespace(execute=AsyncMock())
    engine = SimpleNamespace(connect=context)
    with patch.object(downloads, 'get_business_pg_client', return_value=pg), \
         patch.object(downloads, 'business_database_url', return_value='postgresql+asyncpg://business/analytics'), \
         patch('sqlalchemy.ext.asyncio.create_async_engine', return_value=engine) as create, \
         patch('sqlalchemy.ext.asyncio.async_sessionmaker', return_value=Mock()):
        assert await downloads.connect_pg() is pg
    pg.resolve.assert_awaited_once()
    assert make_url(create.call_args.args[0]).database == 'analytics'
    assert create.call_args.kwargs['poolclass'] is NullPool
    assert create.call_args.kwargs['connect_args']['ssl'] is transport
