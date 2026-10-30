"""Versioned vulnerability keyword files in the existing MinIO bucket."""

from __future__ import annotations

import json
import re
from uuid import uuid4

import yaml
from minio.error import S3Error

from app.storage.minio_client import MinioClient

MAX_FILE_SIZE = 1024 * 1024
VERSION_PREFIX = "tasks/vulnerability_keyword_match/keywords/versions/"
CURRENT_KEY = "tasks/vulnerability_keyword_match/keywords/current.json"
_VERSION_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def normalize_keywords(keywords: list[str]) -> list[str]:
    """Validate literals and keep the first spelling of each distinct keyword."""
    if not isinstance(keywords, list) or len(keywords) > 1000:
        raise ValueError("keywords must be a list of at most 1000 strings")
    result: list[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        if not isinstance(keyword, str) or len(keyword) > 200:
            raise ValueError("Each keyword must be a string of at most 200 characters")
        if any(character in keyword for character in "\r\n\t\v\f\x85\u2028\u2029"):
            raise ValueError("Keywords cannot contain tabs or line breaks")
        keyword = keyword.strip()
        if keyword and keyword.casefold() not in seen:
            result.append(keyword)
            seen.add(keyword.casefold())
    return result


def parse_keyword_file(data: bytes) -> list[str]:
    if len(data) > MAX_FILE_SIZE:
        raise ValueError("Keyword YAML must be at most 1 MiB")
    try:
        document = yaml.safe_load(data.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError, RecursionError) as error:
        raise ValueError("Keyword file must be valid UTF-8 YAML") from error
    if not isinstance(document, dict) or "keywords" not in document:
        raise ValueError("Keyword YAML must contain a keywords list")
    return normalize_keywords(document["keywords"])


def _version_key(version: str) -> str:
    if not isinstance(version, str) or _VERSION_RE.fullmatch(version) is None:
        raise ValueError("Invalid keyword version")
    return f"{VERSION_PREFIX}{version}.yaml"


async def _read_object(minio: MinioClient, key: str, limit: int) -> bytes:
    await minio._ensure_connection()

    def read() -> bytes:
        response = minio._client.get_object(minio._bucket, key)
        try:
            data = response.read(limit + 1)
            if len(data) > limit:
                raise ValueError("Keyword object exceeds the allowed size")
            return data
        finally:
            response.close()
            response.release_conn()

    return await minio._run_sync(read)


async def get_current_version(minio: MinioClient) -> str | None:
    try:
        data = await _read_object(minio, CURRENT_KEY, 1024)
    except S3Error as error:
        if error.code == "NoSuchKey":
            return None
        raise
    try:
        pointer = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError("Invalid current keyword version pointer") from error
    if not isinstance(pointer, dict):
        raise ValueError("Invalid current keyword version pointer")
    version = pointer.get("version")
    _version_key(version)
    return version


async def read_keyword_version(minio: MinioClient, version: str) -> bytes:
    data = await _read_object(minio, _version_key(version), MAX_FILE_SIZE)
    parse_keyword_file(data)
    return data


async def list_keyword_versions(minio: MinioClient) -> list[dict]:
    await minio._ensure_connection()

    def collect() -> list[dict]:
        versions = []
        for item in minio._client.list_objects(
            minio._bucket, prefix=VERSION_PREFIX, recursive=True,
        ):
            name = item.object_name or ""
            version = name[len(VERSION_PREFIX):-5] if name.startswith(VERSION_PREFIX) and name.endswith(".yaml") else ""
            if _VERSION_RE.fullmatch(version) is None:
                continue
            versions.append({
                "version": version,
                "created_at": item.last_modified.isoformat() if item.last_modified else "",
                "size": item.size or 0,
            })
        return sorted(versions, key=lambda version: version["created_at"], reverse=True)

    return await minio._run_sync(collect)


async def _set_current_version(minio: MinioClient, version: str) -> None:
    await minio.upload_bytes_to_key(
        json.dumps({"version": version}).encode("utf-8"), CURRENT_KEY, "application/json",
    )


async def upload_keyword_version(minio: MinioClient, data: bytes) -> str:
    parse_keyword_file(data)
    version = str(uuid4())
    await minio.upload_bytes_to_key(data, _version_key(version), "application/yaml")
    await _set_current_version(minio, version)
    return version


async def activate_keyword_version(minio: MinioClient, version: str) -> None:
    await read_keyword_version(minio, version)
    await _set_current_version(minio, version)
