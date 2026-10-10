"""Normalizer for canonical Twitter records and older twscrape records."""

from __future__ import annotations

import json
from typing import Any

from app.etl.normalizers import register_normalizer
from app.etl.normalizers.base import safe_datetime, safe_str
from app.utils.record_id import resolve_record_id
from app.utils.twitter_record import project_twitter_record


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _stable_id(kind: str, value: str | None) -> int | None:
    if value is None:
        return None
    digest = resolve_record_id({
        "platform": "twitter",
        "entity_type": kind,
        "platform_id": value,
    })
    return int(digest[:16], 16) & 0x7FFFFFFFFFFFFFFF or 1


def _record_id(kind: str, identity: dict[str, Any]) -> str:
    identity = {"platform": "twitter", "entity_type": kind, **identity}
    return resolve_record_id(identity)


def _record_identity(value: str | None) -> dict[str, str]:
    if value is None:
        raise ValueError("Twitter record identity cannot be empty")
    return {"platform_id": value}


def normalize_twitter_account(record: dict[str, Any]) -> dict[str, Any]:
    """Normalize the independent account collection after its assets are downloaded."""
    user = {key: value for key, value in record.items() if key not in {"_id", "_meta"}}
    author_id = safe_str(user["id"])
    meta = record.get("_meta") or {}
    return {
        "data_type": "social_account",
        "record_id": _record_id("account", _record_identity(author_id)),
        "account_id": _stable_id("account", author_id),
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
        "extra": _json({"source_user": user}),
        "captured_at": safe_datetime(meta.get("updated_at")),
    }


def normalize_twitter(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize one tweet without requiring the redundant source_tweet field."""
    source = record if "user" in record and "date" in record else None
    tweet = project_twitter_record(record, source, drop_source=True)
    user = tweet.get("author") or {"id": tweet["author_id"]}
    tweet_id = safe_str(tweet["id"])
    author_id = safe_str(user["id"])
    created_at = safe_datetime(tweet["created_at"])
    references = {item["type"]: safe_str(item["id"])
                  for item in tweet.get("referenced_tweets", [])}
    parent_id = references.get("replied_to")
    metrics = tweet.get("public_metrics") or {}

    content_type = "post"
    target_id = None
    interaction_type = None
    if parent_id is not None:
        content_type = "comment"
        target_id = parent_id
        interaction_type = "comment"
    if references.get("retweeted") is not None:
        content_type = "repost"
        target_id = references["retweeted"]
        interaction_type = "repost"
    if references.get("quoted") is not None:
        content_type = "quote"
        target_id = references["quoted"]
        interaction_type = "quote"

    content_id = _stable_id("content", tweet_id)
    account_id = _stable_id("account", author_id)
    result: list[dict[str, Any]] = [
        {
            "data_type": "social_account",
            "record_id": _record_id("account", _record_identity(author_id)),
            "account_id": account_id,
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
            "extra": _json({"source_user": user}),
            "captured_at": created_at,
        },
        {
            "data_type": "social_content",
            "record_id": _record_id("content", _record_identity(tweet_id)),
            "content_id": content_id,
            "platform_content_id": tweet_id,
            "content_type": content_type,
            "author_account_id": account_id,
            "parent_content_id": _stable_id("content", parent_id),
            "root_content_id": _stable_id("content", safe_str(tweet.get("conversation_id"))),
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
            "extra": _json({key: value for key, value in tweet.items()
                            if key not in {"_id", "_meta"}}),
            "captured_at": created_at,
        },
    ]

    for index, media in enumerate(tweet.get("media") or []):
        media_id = safe_str(media.get("media_key") or media.get("mediaKey") or media.get("url"))
        result.append({
            "data_type": "social_media",
            "record_id": _record_id("media", {"content_id": tweet_id, "media_id": media_id}),
            "content_id": content_id,
            "media_index": index,
            "media_type": safe_str(media["type"]),
            "platform_media_id": media_id,
            "media_url": safe_str(media.get("url")),
            "preview_url": safe_str(media.get("thumbnail_url") or media.get("preview")),
            "width": media.get("width"),
            "height": media.get("height"),
            "duration_ms": media.get("duration"),
            "alt_text": safe_str(media.get("alt_text") or media.get("altText")),
            "extra": _json({"source_media": media}),
        })

    for index, hashtag_text in enumerate(tweet.get("hashtags") or []):
        normalized_text = safe_str(hashtag_text).casefold()
        hashtag_id = _stable_id("hashtag", normalized_text)
        result.extend([
            {
                "data_type": "social_hashtag",
                "record_id": _record_id("hashtag", _record_identity(normalized_text)),
                "hashtag_id": hashtag_id,
                "hashtag_text": hashtag_text,
                "normalized_text": normalized_text,
            },
            {
                "data_type": "social_content_hashtag",
                "record_id": _record_id(
                    "content_hashtag",
                    {"content_id": tweet_id, "hashtag": normalized_text},
                ),
                "content_id": content_id,
                "hashtag_id": hashtag_id,
                "hashtag_index": index,
            },
        ])

    if target_id is not None:
        interaction_identity = {
            "interaction_type": interaction_type,
            "actor_id": author_id,
            "source_content_id": tweet_id,
            "target_content_id": target_id,
        }
        result.append({
            "data_type": "social_interaction",
            "record_id": _record_id("interaction", interaction_identity),
            "interaction_id": _stable_id(
                "interaction", resolve_record_id(interaction_identity),
            ),
            "interaction_type": interaction_type,
            "actor_account_id": account_id,
            "source_content_id": content_id,
            "target_content_id": _stable_id("content", target_id),
            "occurred_at": created_at,
            "extra": _json({"source_tweet_id": tweet_id}),
            "captured_at": created_at,
        })

    return result


async def normalize_twitter_account_linked(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Resolve the Mongo account link when profiles are no longer embedded."""
    if record.get("author") or record.get("source_tweet") or record.get("user"):
        return normalize_twitter(record)
    from motor.motor_asyncio import AsyncIOMotorClient

    from app.config.settings import settings

    client = AsyncIOMotorClient(settings.db_url)
    try:
        account = await client[settings.db_name]["tw_account"].find_one(
            {"id": str(record["author_id"])}, {"_id": 0, "_meta": 0},
        )
        if account is None:
            raise ValueError(f"Twitter account not found: {record['author_id']}")
        return normalize_twitter({**record, "author": account})
    finally:
        client.close()


register_normalizer("account", "twitter", normalize_twitter_account)
register_normalizer("tweet", "twitter", normalize_twitter_account_linked)
