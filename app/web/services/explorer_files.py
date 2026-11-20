"""Resolve only configured MinIO objects referenced by readable physical records."""
from datetime import timedelta
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
    if url.scheme in ('s3', 'minio') and url.netloc == config['bucket']:
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


async def download_link(layer, table, identity, field, *, download=True):
    record = await row_by_key(layer, table, identity, editable=False)
    key = object_key(record['row'].get(field))
    endpoint = urlsplit(storage_configuration()['baseUrl'])
    client = Minio(endpoint.netloc, access_key=settings.minio_access_key,
                   secret_key=settings.minio_secret_key, secure=endpoint.scheme == 'https',
                   http_client=urllib3.PoolManager(timeout=5, retries=False))
    url = await run_in_threadpool(client.presigned_get_object, settings.minio_bucket, key,
        expires=timedelta(minutes=5 if download else 60), response_headers={
            'response-content-type': guess_type(key)[0] or 'application/octet-stream',
            'response-content-disposition': ("attachment" if download else "inline") + "; filename*=UTF-8''" + quote(key.rsplit('/', 1)[-1], safe='')})
    return {"url": url}
