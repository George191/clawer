"""Social-content ODS normalizer for canonical Twitter records."""

from __future__ import annotations

import json
from typing import Any

from app.adapters.utils.twitter_record import (
    project_twitter_record,
    twitter_entity_id,
    twitter_record_id,
    twitter_record_identity,
)
from app.etl.normalizers import register_normalizer
from app.etl.normalizers.base import safe_datetime, safe_str
from app.records.field_names import normalize_record_fields


def _normalize_twitter_content(tweet: dict[str, Any]) -> dict[str, Any]:
    tweet_id = safe_str(tweet["id"])
    user = tweet.get("author") or {"id": tweet["author_id"]}
    author_id = safe_str(user["id"])
    created_at = safe_datetime(tweet["created_at"])
    references = {item["type"]: safe_str(item["id"])
                  for item in tweet.get("referenced_tweets", [])}
    parent_id = references.get("replied_to")
    metrics = tweet.get("public_metrics") or {}
    content_type = "post"
    if parent_id is not None:
        content_type = "comment"
    if references.get("retweeted") is not None:
        content_type = "repost"
    if references.get("quoted") is not None:
        content_type = "quote"

    return {
        "data_type": "social_content",
        "record_id": twitter_record_id("content", twitter_record_identity(tweet_id)),
        "content_id": twitter_entity_id("content", tweet_id),
        "platform_content_id": tweet_id,
        "content_type": content_type,
        "author_account_id": twitter_entity_id("account", author_id),
        "parent_content_id": twitter_entity_id("content", parent_id),
        "root_content_id": twitter_entity_id("content", safe_str(tweet.get("conversation_id"))),
        "text_content": safe_str(tweet.get("text")),
        "language": safe_str(tweet.get("lang")),
        "content_url": safe_str(tweet["url"]),
        "published_at": created_at,
        "edited_at": None,
        "deleted_at": None,
        "reply_count": metrics.get("reply_count"),
        "like_count": metrics.get("like_count"),
        "repost_count": metrics.get("retweet_count"),
        "quote_count": metrics.get("quote_count"),
        "view_count": metrics.get("impression_count"),
        "is_sensitive": tweet.get("possibly_sensitive"),
        "extra": json.dumps(normalize_record_fields({"extra": {
            key: value for key, value in tweet.items() if key not in {"_id", "_meta"}
        }})["extra"], ensure_ascii=False),
        "captured_at": created_at,
    }


def normalize_twitter_content(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize one tweet into its social-table rows."""
    from app.etl.normalizers.social_account import normalize_twitter_author
    from app.etl.normalizers.social_hashtag import normalize_twitter_hashtags
    from app.etl.normalizers.social_interaction import normalize_twitter_interaction
    from app.etl.normalizers.social_media import normalize_twitter_media

    source = record if "user" in record and "date" in record else None
    tweet = project_twitter_record(record, source, drop_source=True)
    user = tweet.get("author") or {"id": tweet["author_id"]}
    created_at = safe_datetime(tweet["created_at"])
    result: list[dict[str, Any]] = [
        normalize_twitter_author(user, created_at),
        _normalize_twitter_content(tweet),
        *normalize_twitter_media(tweet),
        *normalize_twitter_hashtags(tweet),
    ]
    interaction = normalize_twitter_interaction(tweet)
    if interaction is not None:
        result.append(interaction)

    return result


async def normalize_twitter_content_linked(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Resolve the Mongo account link when profiles are no longer embedded."""
    if record.get("author") or record.get("source_tweet") or record.get("user"):
        return normalize_twitter_content(record)
    from motor.motor_asyncio import AsyncIOMotorClient

    from app.config.settings import settings

    client = AsyncIOMotorClient(settings.db_url)
    try:
        account = await client[settings.db_name]["tw_account"].find_one(
            {"id": str(record["author_id"])}, {"_id": 0, "_meta": 0},
        )
        if account is None:
            raise ValueError(f"Twitter account not found: {record['author_id']}")
        return normalize_twitter_content({**record, "author": account})
    finally:
        client.close()


register_normalizer("tweet", "tw_tweet", normalize_twitter_content_linked)
