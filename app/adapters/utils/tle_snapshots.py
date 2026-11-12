"""Durable private TLE response archives, before successful-crawl fingerprints."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4
from urllib.parse import urlsplit, urlunsplit

from app.storage.minio_client import get_minio_client


async def request_tle_snapshot(client, store, url, config, *, source, binary=False, task_id=None, run_id=None, **kwargs):
    if source not in {"space_track", "mike_mccants"}:
        raise ValueError("Unknown TLE archive source")
    if store is None:
        raise ValueError("TLE snapshots require a successful-crawl SnapshotStore")
    # A fixed public source identity excludes cookies, login credentials and headers.
    identity = "tle:" + source
    previous = await store.get_state(identity) or {}
    headers = dict(config.headers)
    if previous.get("etag"):
        headers["If-None-Match"] = previous["etag"]
    elif previous.get("last_modified"):
        headers["If-Modified-Since"] = previous["last_modified"]
    request = config.model_copy(update={"headers": headers})
    response = (await client.download_response(url, request, use_proxy=False) if binary
                else await client.request_page(url, request, return_response=True, **kwargs))
    archive = get_minio_client()
    observed = datetime.now(timezone.utc)
    if response.status_code == 304:
        reference = previous.get("source_snapshot")
        if reference:
            reference = {key: value for key, value in reference.items() if key != "manifest_key"}
        if not reference or not reference.get("key") or not reference.get("sha256"):
            raise ValueError("TLE 304 has no committed archive reference")
        body = await archive.get_object_bytes(reference["key"])
        if body is None or hashlib.sha256(body).hexdigest() != reference["sha256"]:
            raise ValueError("TLE 304 archive is missing or corrupt")
    elif response.status_code == 200:
        body = response.data
        digest = hashlib.sha256(body).hexdigest()
        key = f"tle_snapshots/{source}/bodies/{digest}"
        if await archive.object_exists(key):
            existing = await archive.get_object_bytes(key)
            if existing is None or hashlib.sha256(existing).hexdigest() != digest:
                raise ValueError("Existing immutable TLE archive is corrupt")
        else:
            await archive.upload_bytes_to_key(body, key, "application/octet-stream")
        reference = {"key": key, "sha256": digest, "size": len(body), "source": source}
    else:
        raise ValueError(f"TLE source returned HTTP {response.status_code}")
    # Immutable per-observation manifests retain every body version within a UTC day.
    parts = urlsplit(url)
    public_url = urlunsplit((parts.scheme, parts.hostname or "", parts.path, "", ""))
    manifest = {"observed_at": observed.isoformat(), "status": response.status_code,
                "source": source, "public_url": public_url, "task_id": task_id, "run_id": run_id,
                "source_snapshot": reference}
    manifest_key = f"tle_snapshots/{source}/manifests/{observed:%Y-%m-%d}/{uuid4().hex}.json"
    await archive.upload_bytes_to_key(json.dumps(manifest, sort_keys=True).encode(),
                                      manifest_key, "application/json")
    reference = {**reference, "manifest_key": manifest_key}
    await store.set_state(identity, {
        "etag": response.headers.get("etag", previous.get("etag", "") if response.status_code == 304 else ""),
        "last_modified": response.headers.get("last-modified", previous.get("last_modified", "") if response.status_code == 304 else ""),
        "source_snapshot": reference,
    })
    await store.changed(identity, body)
    # Redis may lag an already committed Mongo event after a partially failed crawl.
    # Always submit the body: only Mongo can decide consecutive business equality.
    return (body if binary else body.decode(config.encoding or "utf-8-sig")), reference


def attach_snapshot(records, reference):
    for record in records:
        record.setdefault("_meta", {})["source_snapshot"] = dict(reference)
    return records
