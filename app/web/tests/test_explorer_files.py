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
    'tweet/twitter/record/photo.jpg', 'tweet/twitter/record/video.mp4',
])
def test_configured_objects(value):
    assert files.object_key(value) == (value if value.startswith('tweet/') else 'docs/report one.pdf')


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
         patch.object(files, 'Minio') as client, \
         patch.object(files, 'run_in_threadpool', AsyncMock(side_effect=lambda fn, *args, **kwargs: fn(*args, **kwargs))):
        sign = client.return_value.presigned_get_object
        sign.return_value = 'https://signed.example/object'
        assert await files.download_link('ts_qa', 'items', {'id': 1}, 'document') == {'url': 'https://signed.example/object'}
        read.assert_awaited_once_with('ts_qa', 'items', {'id': 1}, editable=False)
        assert sign.call_args.args == ('internal', 'report.pdf')
        assert sign.call_args.kwargs['expires'].total_seconds() == 300
        assert sign.call_args.kwargs['response_headers']['response-content-disposition'].startswith('attachment;')
        assert sign.call_args.kwargs['response_headers']['response-content-type'] == 'application/pdf'


@pytest.mark.asyncio
async def test_preview_signs_inline_pdf_without_attachment():
    with patch.object(files, 'row_by_key', AsyncMock(return_value={'row': {'document': 's3://internal/report.pdf'}})), \
         patch.object(files, 'Minio') as client, \
         patch.object(files, 'run_in_threadpool', AsyncMock(side_effect=lambda fn, *args, **kwargs: fn(*args, **kwargs))):
        sign = client.return_value.presigned_get_object
        sign.return_value = 'https://signed.example/object'
        await files.download_link('ts_qa', 'items', {'id': 1}, 'document', download=False)
        assert sign.call_args.kwargs['response_headers']['response-content-disposition'].startswith('inline;')
        assert sign.call_args.kwargs['response_headers']['response-content-type'] == 'application/pdf'
        assert sign.call_args.kwargs['expires'].total_seconds() == 3600


@pytest.mark.asyncio
@pytest.mark.parametrize('name,mime', [('photo.jpg', 'image/jpeg'), ('video.mp4', 'video/mp4')])
async def test_social_media_preview_signs_downloaded_object(name, mime):
    key = 'tweet/twitter/record/' + name
    with patch.object(files, 'row_by_key', AsyncMock(return_value={'row': {'url': key}})), \
         patch.object(files, 'Minio') as client:
        client.return_value.presigned_get_object.return_value = 'https://signed.example/object'
        assert await files.download_link('ts_ods', 'ods_social_media', {'id': 1}, 'url', download=False) == {
            'url': 'https://signed.example/object'}
        sign = client.return_value.presigned_get_object
        assert sign.call_args.args == ('internal', key)
        assert sign.call_args.kwargs['response_headers']['response-content-type'] == mime
        assert sign.call_args.kwargs['response_headers']['response-content-disposition'].startswith('inline;')


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


@pytest.mark.asyncio
async def test_html_preview_reads_only_authorized_record():
    with patch.object(files, 'row_by_key', AsyncMock(side_effect=HTTPException(404, 'Record not found'))), \
         patch.object(files, 'signed_link') as sign:
        with pytest.raises(HTTPException):
            await files.html_preview('ts_qa', 'items', {'id': 1}, 'content_html')
        sign.assert_not_called()


def test_html_preview_restores_media_and_removes_source_styles_and_active_content():
    from lxml import html
    record = {
        'content_html': '<div id="js_content" style="visibility:hidden;opacity:0"><script>evil()</script>'
                        '<style>div{display:none}</style><img data-src="{{img_0}}">'
                        '<video src="{{video_0}}"></video><a href="{{attachment_0}}">Report</a>'
                        '<a href="javascript:evil()" onclick="evil()">Bad</a></div>',
        'images': [{'url': 'acticle/wechat/one.jpg', 'placeholder': '{{img_0}}'}],
        'videos': [{'url': 'news/source/movie.mp4'}],
        'attachments': [{'url': 'news/source/report.pdf'}],
    }
    with patch.object(files, 'signed_link', side_effect=lambda key, **kwargs: 'https://signed.example/' + key) as sign:
        result = files.render_html(record, 'content_html')['html']
    tree = html.fromstring(result)
    assert not tree.xpath('//@style|//script|//style|//@onclick|//@id')
    assert tree.xpath('//img/@src') == ['https://signed.example/acticle/wechat/one.jpg']
    assert tree.xpath('//video/@src') == ['https://signed.example/news/source/movie.mp4']
    assert tree.xpath('//video/@controls') == ['']
    assert tree.xpath('//a/@href') == ['https://signed.example/news/source/report.pdf']
    assert sign.call_count == 3


def test_html_preview_preserves_text_and_external_links_without_signing_them():
    with patch.object(files, 'signed_link') as sign:
        result = files.render_html({'content_html': '<p>Hello <a href="https://example.org/page">world</a></p>'}, 'content_html')
        assert 'Hello' in result['html'] and 'https://example.org/page' in result['html']
        sign.assert_not_called()


@pytest.mark.asyncio
async def test_html_http_contract_authorizes_and_rejects_arbitrary_payloads():
    from app.web.tests.test_transfer_plans import api
    with patch.object(files, 'html_preview', AsyncMock(return_value={'html': '<p>Article</p>'})) as render:
        async with api(False) as client:
            path = '/etl/ts_qa/items/record-html'
            assert (await client.post(path, json={'key': {'id': 1}, 'field': 'content_html', 'url': 'https://outside.example'})).status_code == 422
            render.assert_not_called()
            result = await client.post(path, json={'key': {'id': 1}, 'field': 'content_html'})
            assert result.status_code == 200
            assert result.headers['cache-control'] == 'private, no-store'
            render.assert_awaited_once_with('ts_qa', 'items', {'id': 1}, 'content_html')
