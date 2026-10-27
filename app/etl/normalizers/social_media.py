"""Social-media ODS normalizer for canonical Twitter records."""

from __future__ import annotations

import json
from typing import Any

from app.adapters.utils.twitter_record import twitter_entity_id, twitter_record_id
from app.etl.normalizers.base import apply_asset_path_overrides, safe_local_asset_path, safe_str


def normalize_twitter_media(tweet: dict[str, Any]) -> list[dict[str, Any]]:
    downloaded_tweet, asset_paths = apply_asset_path_overrides(tweet)
    tweet_id = safe_str(tweet["id"])
    content_id = twitter_entity_id("content", tweet_id)
    result: list[dict[str, Any]] = []
    for index, media in enumerate(tweet.get("media") or []):
        media_id = safe_str(media.get("media_key") or media.get("url"))
        result.append({
            "data_type": "social_media",
            "record_id": twitter_record_id("media", {"content_id": tweet_id, "media_id": media_id}),
            "content_id": content_id,
            "media_index": index,
            "media_type": safe_str(media["type"]),
            "platform_media_id": media_id,
            "source_url": safe_str(media.get("url")),
            "url": safe_local_asset_path(downloaded_tweet["media"][index].get("url"))
            if ("media", str(index), "url") in asset_paths else None,
            "width": media.get("width"),
            "height": media.get("height"),
            "duration_ms": media.get("duration"),
            "alt_text": safe_str(media.get("alt_text")),
            "extra": json.dumps({"source_media": media}, ensure_ascii=False),
        })
    return result
