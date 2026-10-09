"""Normalizer for the current twscrape Tweet serialization."""

from __future__ import annotations

import json
from typing import Any

from app.etl.normalizers import register_normalizer
from app.etl.normalizers.base import safe_datetime, safe_str
from app.utils.record_id import resolve_record_id


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


def normalize_twitter(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize one twscrape Tweet dict; missing contract fields are errors."""
    tweet = record
    user = tweet["user"]
    tweet_id = safe_str(tweet["id"])
    author_id = safe_str(user["id"])
    created_at = safe_datetime(tweet["date"])
    parent_id = safe_str(tweet["inReplyToTweetId"])
    retweeted = tweet["retweetedTweet"]
    quoted = tweet["quotedTweet"]

    content_type = "post"
    target_id = None
    interaction_type = None
    if parent_id is not None:
        content_type = "comment"
        target_id = parent_id
        interaction_type = "comment"
    if retweeted is not None:
        content_type = "repost"
        target_id = safe_str(retweeted["id"])
        interaction_type = "repost"
    if quoted is not None:
        content_type = "quote"
        target_id = safe_str(quoted["id"])
        interaction_type = "quote"

    content_id = _stable_id("content", tweet_id)
    account_id = _stable_id("account", author_id)
    result: list[dict[str, Any]] = [
        {
            "data_type": "social_account",
            "record_id": _record_id("account", _record_identity(author_id)),
            "account_id": account_id,
            "platform": "twitter",
            "platform_account_id": author_id,
            "username": safe_str(user["username"]),
            "display_name": safe_str(user["displayname"]),
            "profile_url": safe_str(user["url"]),
            "avatar_url": safe_str(user["profileImageUrl"]),
            "bio": safe_str(user["rawDescription"]),
            "account_type": "user",
            "is_verified": user["verified"],
            "is_private": user["protected"],
            "account_created_at": safe_datetime(user["created"]),
            "follower_count": user["followersCount"],
            "following_count": user["friendsCount"],
            "content_count": user["statusesCount"],
            "location": safe_str(user["location"]),
            "language": safe_str(user["lang"]),
            "extra": _json({"source_user": user}),
            "captured_at": created_at,
        },
        {
            "data_type": "social_content",
            "record_id": _record_id("content", _record_identity(tweet_id)),
            "content_id": content_id,
            "platform": "twitter",
            "platform_content_id": tweet_id,
            "content_type": content_type,
            "author_account_id": account_id,
            "parent_content_id": _stable_id("content", parent_id),
            "root_content_id": _stable_id("content", safe_str(tweet["conversationId"])),
            "text_content": safe_str(tweet["rawContent"]),
            "language": safe_str(tweet["lang"]),
            "content_url": safe_str(tweet["url"]),
            "published_at": created_at,
            "edited_at": None,
            "deleted_at": None,
            "reply_count": tweet["replyCount"],
            "like_count": tweet["likeCount"],
            "repost_count": tweet["retweetCount"],
            "quote_count": tweet["quoteCount"],
            "view_count": tweet["viewCount"],
            "is_sensitive": tweet["possiblySensitive"],
            "extra": _json({"source_tweet": tweet}),
            "captured_at": created_at,
        },
    ]

    for index, media in enumerate(tweet["media"]):
        media_id = safe_str(media["mediaKey"])
        result.append({
            "data_type": "social_media",
            "record_id": _record_id("media", {"content_id": tweet_id, "media_id": media_id}),
            "content_id": content_id,
            "media_index": index,
            "media_type": safe_str(media["type"]),
            "platform_media_id": media_id,
            "media_url": safe_str(media["url"]),
            "preview_url": safe_str(media["preview"]),
            "width": media["width"],
            "height": media["height"],
            "duration_ms": media["duration"],
            "alt_text": safe_str(media["altText"]),
            "extra": _json({"source_media": media}),
        })

    for index, hashtag_text in enumerate(tweet["hashtags"]):
        normalized_text = safe_str(hashtag_text).casefold()
        hashtag_id = _stable_id("hashtag", normalized_text)
        result.extend([
            {
                "data_type": "social_hashtag",
                "record_id": _record_id("hashtag", _record_identity(normalized_text)),
                "hashtag_id": hashtag_id,
                "platform": "twitter",
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
            "platform": "twitter",
            "interaction_type": interaction_type,
            "actor_account_id": account_id,
            "source_content_id": content_id,
            "target_content_id": _stable_id("content", target_id),
            "occurred_at": created_at,
            "extra": _json({"source_tweet_id": tweet_id}),
            "captured_at": created_at,
        })

    return result


register_normalizer("twitter", "twitter", normalize_twitter)
