"""Social-account ODS normalizer for Twitter profiles."""

from __future__ import annotations

import json
from typing import Any

from app.etl.normalizers import register_normalizer
from app.etl.normalizers.base import safe_datetime, safe_str
from app.utils.twitter_record import twitter_entity_id, twitter_record_id, twitter_record_identity


def normalize_twitter_author(user: dict[str, Any], captured_at: Any) -> dict[str, Any]:
    author_id = safe_str(user["id"])
    return {
        "data_type": "social_account",
        "record_id": twitter_record_id("account", twitter_record_identity(author_id)),
        "account_id": twitter_entity_id("account", author_id),
        "platform_account_id": author_id,
        "username": safe_str(user.get("username")),
        "display_name": safe_str(user.get("display_name")),
        "profile_url": safe_str(user.get("url")),
        "avatar_url": safe_str(user.get("avatar_url")),
        "bio": safe_str(user.get("bio")),
        "account_type": "user",
        "is_verified": user.get("verified"),
        "is_private": user.get("protected"),
        "account_created_at": safe_datetime(user.get("created")),
        "follower_count": user.get("followers_count"),
        "following_count": user.get("friends_count"),
        "content_count": user.get("statuses_count"),
        "location": safe_str(user.get("location")),
        "language": safe_str(user.get("lang")),
        "extra": json.dumps({"source_user": user}, ensure_ascii=False),
        "captured_at": captured_at,
    }


def normalize_twitter_account(record: dict[str, Any]) -> dict[str, Any]:
    """Normalize the independent account collection after its assets are downloaded."""
    user = {key: value for key, value in record.items() if key not in {"_id", "_meta"}}
    meta = record.get("_meta") or {}
    return normalize_twitter_author(user, safe_datetime(meta.get("updated_at")))


register_normalizer("account", "twitter", normalize_twitter_account)
