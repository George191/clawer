from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.web.services import explorer_files as files


@pytest.fixture(autouse=True)
def storage():
    with patch.object(files, 'settings', SimpleNamespace(
        minio_endpoint='minio.internal:9000', minio_public_endpoint='files.example:9000',
        minio_bucket='internal', minio_secure=False, minio_access_key='test', minio_secret_key='test',
    )):
        yield


@pytest.mark.parametrize('value', [
    's3://internal/docs/report%20one.pdf', 'minio://internal/docs/report%20one.pdf',
    '/internal/docs/report%20one.pdf', 'http://minio.internal:9000/internal/docs/report%20one.pdf',
    'http://files.example:9000/internal/docs/report%20one.pdf',
])
def test_configured_objects(value):
    assert files.object_key(value) == 'docs/report one.pdf'


@pytest.mark.parametrize('value', [None, {}, 'javascript:alert(1)', 'https://outside.example/internal/x',
    's3://another-bucket/x', 'http://files.example:9000/another/x',
    'http://user:pass@files.example:9000/internal/x', 'http://[invalid', '/internal/'])
def test_rejects_foreign_or_invalid_objects(value):
    with pytest.raises(HTTPException) as error:
        files.object_key(value)
    assert error.value.status_code == 422


@pytest.mark.asyncio
async def test_download_uses_value_from_readable_record_and_forces_attachment():
    with patch.object(files, 'row_by_key', AsyncMock(return_value={'row': {'document': 's3://internal/report.pdf'}})) as read, \
         patch.object(files, 'run_in_threadpool', AsyncMock(return_value='https://signed.example/object')) as sign, \
         patch.object(files, 'Minio'):
        assert await files.download_link('ts_qa', 'items', {'id': 1}, 'document') == {'url': 'https://signed.example/object'}
        read.assert_awaited_once_with('ts_qa', 'items', {'id': 1}, editable=False)
        assert sign.call_args.args[1:] == ('internal', 'report.pdf')
        assert sign.call_args.kwargs['expires'].total_seconds() == 300
        assert sign.call_args.kwargs['response_headers']['response-content-disposition'].startswith('attachment;')
        assert sign.call_args.kwargs['response_headers']['response-content-type'] == 'application/pdf'


@pytest.mark.asyncio
async def test_preview_signs_inline_pdf_without_attachment():
    with patch.object(files, 'row_by_key', AsyncMock(return_value={'row': {'document': 's3://internal/report.pdf'}})), \
         patch.object(files, 'run_in_threadpool', AsyncMock(return_value='https://signed.example/object')) as sign, \
         patch.object(files, 'Minio'):
        await files.download_link('ts_qa', 'items', {'id': 1}, 'document', download=False)
        assert sign.call_args.kwargs['response_headers']['response-content-disposition'].startswith('inline;')
        assert sign.call_args.kwargs['response_headers']['response-content-type'] == 'application/pdf'
        assert sign.call_args.kwargs['expires'].total_seconds() == 3600


@pytest.mark.asyncio
async def test_unreadable_record_never_signs():
    with patch.object(files, 'row_by_key', AsyncMock(side_effect=HTTPException(404, 'Record not found'))), \
         patch.object(files, 'run_in_threadpool', AsyncMock()) as sign:
        with pytest.raises(HTTPException):
            await files.download_link('ts_qa', 'items', {'id': 1}, 'document')
        sign.assert_not_called()


@pytest.mark.asyncio
async def test_file_http_contract_rejects_arbitrary_urls_and_signs_record_field():
    from app.web.tests.test_transfer_plans import api

    with patch.object(files, 'download_link', AsyncMock(return_value={'url': 'https://signed.example/object'})) as sign:
        async with api(False) as client:
            config = await client.get('/etl/object-storage')
            assert config.status_code == 200
            assert set(config.json()) == {'baseUrl', 'bucket', 'origins'}
            path = '/etl/ts_qa/items/record-file'
            assert (await client.post(path, json={'key': {'id': 1}, 'field': 'document', 'url': 'https://outside.example'})).status_code == 422
            sign.assert_not_called()
            result = await client.post(path, json={'key': {'id': 1}, 'field': 'document', 'download': False})
            assert result.status_code == 200
            assert result.headers['cache-control'] == 'private, no-store'
            sign.assert_awaited_once_with('ts_qa', 'items', {'id': 1}, 'document', download=False)
