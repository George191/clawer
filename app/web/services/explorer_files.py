"""Resolve only configured MinIO objects referenced by readable physical records."""
import json
import re
from datetime import timedelta
from html import escape

from lxml import html as lxml_html
from mimetypes import guess_type
from urllib.parse import quote, unquote, urlsplit

from fastapi import HTTPException
from minio import Minio
import urllib3
from starlette.concurrency import run_in_threadpool

from app.config.settings import settings
from app.web.services.explorer_rows import row_by_key


def storage_configuration():
    def address(endpoint):
        return endpoint.rstrip('/') if '://' in endpoint else ('https://' if settings.minio_secure else 'http://') + endpoint.rstrip('/')
    return {"baseUrl": address(settings.minio_public_endpoint or settings.minio_endpoint),
            "origins": list({address(v) for v in (settings.minio_endpoint, settings.minio_public_endpoint) if v}),
            "bucket": settings.minio_bucket}


def object_key(value):
    if not isinstance(value, str):
        raise HTTPException(422, "This property is not a MinIO file")
    config = storage_configuration()
    try:
        url = urlsplit(value)
    except ValueError as exc:
        raise HTTPException(422, "This property is not a configured MinIO file") from exc
    if re.fullmatch(r'[a-z][a-z0-9_]*/[a-z][a-z0-9_]*/[^?#]+', value):
        key = unquote(value)
    elif url.scheme in ('s3', 'minio') and url.netloc == config['bucket']:
        key = unquote(url.path.lstrip('/'))
    elif value.startswith('/' + config['bucket'] + '/'):
        key = unquote(value[len(config['bucket']) + 2:])
    elif url.scheme in ('http', 'https') and not url.username and not url.password:
        if url.netloc not in {urlsplit(v).netloc for v in config['origins']} or not url.path.startswith('/' + config['bucket'] + '/'):
            raise HTTPException(422, "This property is not a configured MinIO file")
        key = unquote(url.path[len(config['bucket']) + 2:])
    else:
        raise HTTPException(422, "This property is not a configured MinIO file")
    if not key or key.endswith('/'):
        raise HTTPException(422, "The MinIO object key is empty")
    return key


def storage_client():
    endpoint = settings.minio_endpoint
    endpoint = urlsplit(endpoint if '://' in endpoint else ('https://' if settings.minio_secure else 'http://') + endpoint)
    return Minio(endpoint.netloc, access_key=settings.minio_access_key,
                 secret_key=settings.minio_secret_key, secure=endpoint.scheme == 'https',
                 http_client=urllib3.PoolManager(timeout=5, retries=False))


def signed_link(key, *, download=False, client=None):
    client = client or storage_client()
    endpoint = urlsplit(storage_configuration()['baseUrl'])
    # Discover the bucket region internally; the public host is only used to sign URLs.
    signer = Minio(endpoint.netloc, access_key=settings.minio_access_key,
                   secret_key=settings.minio_secret_key, secure=endpoint.scheme == 'https',
                   region=client._get_region(settings.minio_bucket))
    url = signer.presigned_get_object(settings.minio_bucket, key,
        expires=timedelta(minutes=5 if download else 60), response_headers={
            'response-content-type': guess_type(key)[0] or 'application/octet-stream',
            'response-content-disposition': ("attachment" if download else "inline") + "; filename*=UTF-8''" + quote(key.rsplit('/', 1)[-1], safe='')})
    return url


async def download_link(layer, table, identity, field, *, download=True, path=()):
    record = await row_by_key(layer, table, identity, editable=False)
    return await record_file_link(record['row'], field, download=download, path=path)


async def record_file_link(record, field, *, download=True, path=()):
    """Sign a configured resource after the caller has authorized its record and field."""
    value = record.get(field)
    if path and isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError as exc:
            raise HTTPException(422, "This property is not a resource list or object") from exc
    for segment in path:
        if type(segment) is int and isinstance(value, list) and 0 <= segment < len(value):
            value = value[segment]
        elif type(segment) is str and isinstance(value, dict) and segment in value:
            value = value[segment]
        else:
            raise HTTPException(422, "The resource path does not exist in this property")
    key = object_key(value)
    return {"url": await run_in_threadpool(signed_link, key, download=download)}


def render_html(record, field):
    content = record.get(field)
    if not isinstance(content, str) or not content.strip():
        raise HTTPException(422, "This property has no HTML content")
    resources = {}
    for name, singular in (("images", "img"), ("videos", "video"), ("audios", "audio"), ("attachments", "attachment")):
        items = record.get(name) or []
        if isinstance(items, str):
            items = json.loads(items)
        if not isinstance(items, list):
            continue
        for index, item in enumerate(items):
            url = item.get('url') if isinstance(item, dict) else item
            if not isinstance(url, str) or not url:
                continue
            resources['{{' + singular + '_' + str(index) + '}}'] = url
            if isinstance(item, dict) and item.get('placeholder'):
                resources[item['placeholder']] = url
    signed = {}
    client = storage_client()

    def resolve(value):
        value = resources.get(value, value)
        if value in signed:
            return signed[value]
        try:
            key = object_key(value)
        except HTTPException:
            return value if value.startswith(('https://', 'http://', 'data:image/')) else None
        signed[value] = signed_link(key, client=client)
        return signed[value]

    try:
        tree = lxml_html.fragment_fromstring(content, create_parent='div')
    except (ValueError, lxml_html.etree.ParserError) as exc:
        raise HTTPException(422, "Invalid HTML content") from exc
    for node in list(tree.iterdescendants()):
        if not isinstance(node.tag, str):
            continue
        if node.tag in ('script', 'style', 'link', 'meta', 'iframe', 'object', 'embed', 'form', 'base'):
            node.drop_tree()
            continue
        lazy = node.get('data-src')
        if lazy and node.tag in ('img', 'video', 'audio', 'source'):
            node.set('src', lazy)
        for attr in list(node.attrib):
            if attr not in ('src', 'href', 'poster', 'alt', 'title', 'colspan', 'rowspan'):
                del node.attrib[attr]
        for attr in ('src', 'href', 'poster'):
            value = node.get(attr)
            if value:
                resolved = resolve(value)
                if resolved:
                    node.set(attr, resolved)
                else:
                    del node.attrib[attr]
        if node.tag == 'a':
            node.set('target', '_blank')
            node.set('rel', 'noopener noreferrer')
        if node.tag in ('video', 'audio'):
            node.set('controls', '')
            node.set('preload', 'metadata')
    return {"html": escape(tree.text or '', quote=False) + ''.join(
        lxml_html.tostring(node, encoding='unicode', method='html') for node in tree)}


async def html_preview(layer, table, identity, field):
    record = await row_by_key(layer, table, identity, editable=False)
    return await run_in_threadpool(render_html, record['row'], field)
