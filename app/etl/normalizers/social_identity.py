"""Stable social-table identities, independent of collection record parsing."""

from __future__ import annotations

from typing import Any

from app.utils.record_id import resolve_record_id


def twitter_entity_id(kind: str, value: str | None) -> int | None:
    if value is None:
        return None
    digest = resolve_record_id({
        "platform": "twitter",
        "entity_type": kind,
        "platform_id": value,
    })
    return int(digest[:16], 16) & 0x7FFFFFFFFFFFFFFF or 1


def twitter_record_id(kind: str, identity: dict[str, Any]) -> str:
    identity = {"platform": "twitter", "entity_type": kind, **identity}
    return resolve_record_id(identity)


def twitter_record_identity(value: str | None) -> dict[str, str]:
    if value is None:
        raise ValueError("Twitter record identity cannot be empty")
    return {"platform_id": value}
