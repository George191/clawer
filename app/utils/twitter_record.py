"""Canonical Twitter records shared by released adapters and history migration."""
import re
from copy import deepcopy
from datetime import datetime, timezone

TWITTER_FIELDS = {
    "id": "id", "id_str": "id", "url": "url", "date": "created_at",
    "lang": "lang", "rawContent": "text", "conversationId": "conversation_id",
    "conversationIdStr": "conversation_id", "possibly_sensitive": "possibly_sensitive",
    "possiblySensitive": "possibly_sensitive", "mentionedUsers": "mentioned_users",
    "inReplyToUser": "in_reply_to_user", "inReplyToScreenName": "in_reply_to_screen_name",
    "inReplyToTweetId": "in_reply_to_tweet_id", "inReplyToTweetIdStr": "in_reply_to_tweet_id",
}
TWITTER_METRICS = {
    "replyCount": "reply_count", "retweetCount": "retweet_count", "likeCount": "like_count",
    "quoteCount": "quote_count", "bookmarkedCount": "bookmark_count", "viewCount": "impression_count",
}


def _snake_case(key):
    key = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", key)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key).lower()


def _nonempty(value):
    return value is not None and value != "" and value != [] and value != {}


def _merge(old, new):
    result = deepcopy(old)
    for key, value in new.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        elif _nonempty(value):
            result[key] = deepcopy(value)
    return result


def _best_media(value):
    if isinstance(value, list):
        return [_best_media(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _best_media(item) for key, item in value.items()}
    if result.get("variants"):
        result["variants"] = [max(result["variants"], key=lambda item: item.get("bitrate") or 0)]
    return result


def _user_record(user):
    aliases = {"displayname": "display_name", "rawDescription": "bio",
               "profileImageUrl": "avatar_url", "profileBannerUrl": "banner_url"}
    result = {}
    for key, value in user.items():
        if key in {"_type", "id_str"} or not _nonempty(value):
            continue
        result[aliases.get(key, _snake_case(key))] = str(value) if key == "id" else deepcopy(value)
    if "id" not in result and user.get("id_str"):
        result["id"] = str(user["id_str"])
    return result


def _media_records(media, existing):
    if isinstance(media, list):
        return _best_media(media)
    items = []
    for group, kind in (("photos", "photo"), ("videos", "video"), ("animated", "animated_gif")):
        for raw in media.get(group) or []:
            item = _best_media(raw)
            variants = item.pop("variants", [])
            if variants:
                item.update(variants[0])
            item = {_snake_case(k): v for k, v in item.items() if _nonempty(v)}
            if "video_url" in item:
                item["url"] = item.pop("video_url")
            item["type"] = kind
            previous = next((x for x in existing if x.get("url") == item.get("url")), {})
            items.append(_merge(previous, item))
    return items


def project_twitter_record(record, source=None, *, drop_source=False, ancestors=frozenset()):
    """Project every nonempty serialized field, recursively, without mutating the input."""
    result = deepcopy(record)
    source = source if source is not None else record.get("source_tweet", {})
    current_id = str(source.get("id_str") or source.get("id") or record.get("id") or "")
    path = ancestors | {current_id} if current_id else ancestors
    metrics = dict(result.get("public_metrics") or {})
    for key, value in source.items():
        if not _nonempty(value) or key in {"_type", "retweetedTweet", "quotedTweet"}:
            continue
        if key in TWITTER_METRICS:
            metrics[TWITTER_METRICS[key]] = value
        elif key == "user":
            result["author"] = _merge(_user_record(result.get("author") or {}), _user_record(value))
            result["author_id"] = str(value.get("id_str") or value["id"])
        elif key == "media":
            result["media"] = _media_records(value, result.get("media") or [])
            # Do not discard future media groups unknown to this twscrape version.
            extra = {k: _best_media(v) for k, v in value.items()
                     if k not in {"photos", "videos", "animated"} and _nonempty(v)} if isinstance(value, dict) else {}
            if extra:
                result["media_extra"] = extra
        elif key == "editControl":
            ids = value.get("edit_tweet_ids")
            if ids:
                result["edit_history_tweet_ids"] = [str(x) for x in ids]
            other = {k: v for k, v in value.items() if k != "edit_tweet_ids" and _nonempty(v)}
            if other:
                result["edit_control"] = _merge(result.get("edit_control") or {}, other)
        else:
            target = TWITTER_FIELDS.get(key, _snake_case(key))
            if target in {"id", "conversation_id", "in_reply_to_tweet_id"}:
                value = str(value)
            elif key == "date":
                parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                value = parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            elif key == "inReplyToUser":
                value = _user_record(value)
                if value.get("id"):
                    result["in_reply_to_user_id"] = value["id"]
            value = _best_media(value)
            result[target] = _merge(result[target], value) if isinstance(result.get(target), dict) and isinstance(value, dict) else deepcopy(value)
    if metrics:
        result["public_metrics"] = metrics
    references = {}
    raw_references = {}
    for original in result.get("referenced_tweets") or []:
        ref = deepcopy(original)
        ref["id"] = str(ref["id"])
        identity = (ref["type"], ref["id"])
        references[identity] = _merge(references.get(identity, {}), ref)
    reply_id = result.get("in_reply_to_tweet_id")
    if reply_id:
        references.setdefault(("replied_to", str(reply_id)), {"type": "replied_to", "id": str(reply_id)})
    for raw_key, old_key, relation in (("retweetedTweet", "retweeted_tweet", "retweeted"),
                                        ("quotedTweet", "quoted_tweet", "quoted")):
        raw = source.get(raw_key) or {}
        old = result.get(old_key) or {}
        related_id = str(raw.get("id_str") or raw.get("id") or old.get("id") or "")
        if not related_id:
            continue
        ref = references.setdefault((relation, related_id), {"type": relation, "id": related_id})
        ref["tweet"] = _merge(ref.get("tweet") or {}, old)
        if raw:
            raw_references[(relation, related_id)] = raw
    for ref in references.values():
        nested = ref.get("tweet")
        if ref["id"] in path:
            ref.pop("tweet", None)  # A graph cycle is represented by its relationship ID.
        elif isinstance(nested, dict):
            raw = raw_references.get((ref["type"], ref["id"]))
            ref["tweet"] = project_twitter_record(nested, raw, drop_source=drop_source, ancestors=path)
    if references:
        result["referenced_tweets"] = list(references.values())
    for key in ("quoted_tweet", "retweeted_tweet"):
        result.pop(key, None)
    if not result.get("mentioned_users"):
        result.pop("mentioned_users", None)
    if drop_source:
        result.pop("source_tweet", None)
    result.pop("download_urls", None)
    return result
