"""Social-interaction ODS normalizer for canonical Twitter records."""

from __future__ import annotations

import json
from typing import Any

from app.adapters.utils.twitter_record import twitter_entity_id, twitter_record_id
from app.etl.normalizers.base import safe_datetime, safe_str
from app.utils.record_id import resolve_record_id


def normalize_twitter_interaction(tweet: dict[str, Any]) -> dict[str, Any] | None:
    references = {item["type"]: safe_str(item["id"])
                  for item in tweet.get("referenced_tweets", [])}
    target_id = references.get("replied_to")
    interaction_type = "comment" if target_id is not None else None
    if references.get("retweeted") is not None:
        target_id = references["retweeted"]
        interaction_type = "repost"
    if references.get("quoted") is not None:
        target_id = references["quoted"]
        interaction_type = "quote"
    if target_id is None:
        return None

    tweet_id = safe_str(tweet["id"])
    user = tweet.get("author") or {"id": tweet["author_id"]}
    author_id = safe_str(user["id"])
    created_at = safe_datetime(tweet["created_at"])
    interaction_identity = {
        "interaction_type": interaction_type,
        "actor_id": author_id,
        "source_content_id": tweet_id,
        "target_content_id": target_id,
    }
    return {
        "data_type": "social_interaction",
        "record_id": twitter_record_id("interaction", interaction_identity),
        "interaction_id": twitter_entity_id(
            "interaction", resolve_record_id(interaction_identity),
        ),
        "interaction_type": interaction_type,
        "actor_account_id": twitter_entity_id("account", author_id),
        "source_content_id": twitter_entity_id("content", tweet_id),
        "target_content_id": twitter_entity_id("content", target_id),
        "occurred_at": created_at,
        "extra": json.dumps({"source_tweet_id": tweet_id}, ensure_ascii=False),
        "captured_at": created_at,
    }
