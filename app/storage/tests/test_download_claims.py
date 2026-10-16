"""Download claim regression tests without a running MongoDB server."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.storage.mongo_storage import MongoStorage


def _value(document, path):
    value = document
    for part in path.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value


def _matches(document, query):
    for field, expected in query.items():
        actual = _value(document, field)
        if isinstance(expected, dict):
            if "$in" in expected and actual not in expected["$in"]:
                return False
        elif actual != expected:
            return False
    return True


class _Cursor:
    def __init__(self, documents):
        self.documents = deepcopy(documents)

    def sort(self, fields):
        self.documents.sort(key=lambda doc: tuple(_value(doc, field) for field, _ in fields))
        return self

    def limit(self, limit):
        self.documents = self.documents[:limit]
        return self

    async def to_list(self, length):
        # Let competing workers read the same candidates before either updates them.
        await asyncio.sleep(0)
        return self.documents[:length]


class _Collection:
    def __init__(self, documents):
        self.documents = deepcopy(documents)
        self.updates = []

    def find(self, query, projection=None):
        return _Cursor([doc for doc in self.documents if _matches(doc, query)])

    def _update(self, query, update, *, one=False):
        self.updates.append((deepcopy(query), deepcopy(update)))
        matched = 0
        for document in self.documents:
            if not _matches(document, query):
                continue
            matched += 1
            for operator, fields in update.items():
                for path, value in fields.items():
                    parent = document
                    parts = path.split(".")
                    for part in parts[:-1]:
                        parent = parent.setdefault(part, {})
                    key = parts[-1]
                    if operator == "$set":
                        parent[key] = value
                    elif operator == "$inc":
                        parent[key] = parent.get(key, 0) + value
                    elif operator == "$unset":
                        parent.pop(key, None)
            if one:
                break
        return SimpleNamespace(matched_count=matched)

    async def update_many(self, query, update):
        return self._update(query, update)

    async def update_one(self, query, update):
        return self._update(query, update, one=True)


def _record(record_id, status="pending", **metadata):
    return {
        "_id": record_id,
        "_meta": {
            "record_id": record_id,
            "template": "example",
            "download_status": status,
            "updated_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            **metadata,
        },
    }


def _storage(*records):
    collection = _Collection(records)
    storage = MongoStorage()
    storage._db = {"example": collection}
    storage._get_collection = AsyncMock(return_value=collection)
    return storage, collection


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata", [
    {"download_claimed_at": datetime.now(timezone.utc)},
    {"download_claimed_at": datetime(2020, 1, 1, tzinfo=timezone.utc)},
    {},
    {"download_claimed_at": None},
], ids=["active", "old", "missing-time", "null-time"])
async def test_downloading_claim_is_never_taken_over(metadata):
    storage, collection = _storage(_record(
        "active", "downloading",
        download_claim_token="original",
        **metadata,
    ))

    assert await storage.get_pending_downloads("example") == []
    assert collection.documents[0]["_meta"]["download_claim_token"] == "original"
    assert collection.updates == []


@pytest.mark.asyncio
async def test_only_pending_records_are_claimed():
    storage, collection = _storage(
        _record("pending"),
        _record("completed", "downloaded"),
        _record("failed", "failed"),
    )

    claimed = await storage.get_pending_downloads("example")

    assert [doc["_meta"]["record_id"] for doc in claimed] == ["pending"]
    assert claimed[0]["_meta"]["download_attempts"] == 1
    assert claimed[0]["_meta"]["download_status"] == "downloading"
    assert collection.documents[1]["_meta"]["download_status"] == "downloaded"
    assert collection.documents[2]["_meta"]["download_status"] == "failed"


@pytest.mark.asyncio
async def test_competing_claims_only_return_the_winning_token():
    storage, collection = _storage(_record("shared"))

    results = await asyncio.gather(
        storage.get_pending_downloads("example"),
        storage.get_pending_downloads("example"),
    )

    assert sorted(map(len, results)) == [0, 1]
    winning_token = collection.documents[0]["_meta"]["download_claim_token"]
    assert next(batch[0] for batch in results if batch)["_meta"]["download_claim_token"] == winning_token
    assert collection.documents[0]["_meta"]["download_attempts"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["update_file_status", "update_download_result"])
async def test_old_token_cannot_write_after_new_claim(method):
    storage, collection = _storage(_record(
        "recovered", "pending",
        download_claim_token="old",
    ))
    claimed = await storage.get_pending_downloads("example")
    current_token = claimed[0]["_meta"]["download_claim_token"]
    if method == "update_file_status":
        saved = await storage.update_file_status("example", "recovered", "failed", "old")
    else:
        saved = await storage.update_download_result(
            "example", "recovered", {"asset.path": "old-file"}, "downloaded", claim_token="old",
        )

    assert saved is False
    assert collection.documents[0]["_meta"]["download_status"] == "downloading"
    assert collection.documents[0]["_meta"]["download_claim_token"] == current_token
    assert "asset" not in collection.documents[0]

    if method == "update_file_status":
        saved = await storage.update_file_status(
            "example", "recovered", "downloaded", current_token,
        )
    else:
        saved = await storage.update_download_result(
            "example", "recovered", {"asset.path": "new-file"}, "downloaded",
            claim_token=current_token,
        )
    assert saved is True
    assert collection.documents[0]["_meta"]["download_status"] == "downloaded"
    assert "download_claim_token" not in collection.documents[0]["_meta"]
