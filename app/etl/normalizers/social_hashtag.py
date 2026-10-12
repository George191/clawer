"""Social-hashtag and content-link ODS normalizers for Twitter records."""

from __future__ import annotations

from typing import Any

from app.etl.normalizers.base import safe_str
from app.utils.twitter_record import twitter_entity_id, twitter_record_id, twitter_record_identity


def normalize_twitter_hashtags(tweet: dict[str, Any]) -> list[dict[str, Any]]:
    tweet_id = safe_str(tweet["id"])
    content_id = twitter_entity_id("content", tweet_id)
    result: list[dict[str, Any]] = []
    for index, hashtag_text in enumerate(tweet.get("hashtags") or []):
        normalized_text = safe_str(hashtag_text).casefold()
        hashtag_id = twitter_entity_id("hashtag", normalized_text)
        result.extend([
            {
                "data_type": "social_hashtag",
                "record_id": twitter_record_id("hashtag", twitter_record_identity(normalized_text)),
                "hashtag_id": hashtag_id,
                "hashtag_text": hashtag_text,
                "normalized_text": normalized_text,
            },
            {
                "data_type": "social_content_hashtag",
                "record_id": twitter_record_id(
                    "content_hashtag",
                    {"content_id": tweet_id, "hashtag": normalized_text},
                ),
                "content_id": content_id,
                "hashtag_id": hashtag_id,
                "hashtag_index": index,
            },
        ])
    return result
