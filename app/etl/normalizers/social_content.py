"""Social-content ODS normalizer for canonical Twitter records."""

from __future__ import annotations

import json
from typing import Any

from app.etl.normalizers import register_normalizer
from app.etl.normalizers.base import safe_datetime, safe_str
from app.etl.normalizers.social_identity import (
    twitter_entity_id,
    twitter_record_id,
    twitter_record_identity,
)
from app.records.field_names import normalize_record_fields


def _normalize_twitter_content(tweet: dict[str, Any]) -> dict[str, Any]:
    tweet_id = safe_str(tweet["id"])
    author_id = safe_str(tweet["author_id"])
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
    from app.etl.normalizers.social_hashtag import normalize_twitter_hashtags
    from app.etl.normalizers.social_interaction import normalize_twitter_interaction
    from app.etl.normalizers.social_media import normalize_twitter_media

    tweet = record
    result: list[dict[str, Any]] = [
        _normalize_twitter_content(tweet),
        *normalize_twitter_media(tweet),
        *normalize_twitter_hashtags(tweet),
    ]
    interaction = normalize_twitter_interaction(tweet)
    if interaction is not None:
        result.append(interaction)

    return result


register_normalizer("tweet", "tweet_twitter", normalize_twitter_content)
