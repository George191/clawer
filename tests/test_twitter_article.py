from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.adapters.twitter import (
    TwitterAdapter,
    _extract_tweet_article,
    _TwitterProxyRetryError,
)


def _tweet() -> SimpleNamespace:
    return SimpleNamespace(
        id=1879992969727537510,
        rawContent="https://t.co/TYQOicKtay",
        url="https://x.com/COMSPOC_OPS/status/1879992969727537510",
        user=SimpleNamespace(id=1834646360366125056),
        conversationId=1879992969727537510,
        date=datetime(2025, 1, 16, 20, 43, 47, tzinfo=timezone.utc),
        lang="zxx",
        editControl={"edit_tweet_ids": ["1879992969727537510"]},
        inReplyToUser=None,
        possibly_sensitive=False,
        replyCount=1,
        retweetCount=2,
        likeCount=4,
        quoteCount=0,
        bookmarkedCount=0,
        viewCount=513,
        inReplyToTweetId=None,
        retweetedTweet=None,
        quotedTweet=None,
        links=[
            SimpleNamespace(
                url="https://x.com/i/article/1879975055066607616",
                text="x.com/i/article/1879975055066607616",
                tcourl="https://t.co/TYQOicKtay",
            )
        ],
    )


def test_extract_article_plain_text_for_matching_tweet():
    payload = {
        "data": {
            "threaded_conversation_with_injections_v2": {
                "instructions": [{
                    "entries": [{
                        "content": {
                            "itemContent": {
                                "tweet_results": {
                                    "result": {
                                        "rest_id": "1879992969727537510",
                                        "legacy": {"full_text": "https://t.co/TYQOicKtay"},
                                        "article": {
                                            "article_results": {
                                                "result": {
                                                    "rest_id": "1879975055066607616",
                                                    "title": "Monitoring SJ-25",
                                                    "preview_text": "Preview",
                                                    "plain_text": "Full article body",
                                                }
                                            }
                                        },
                                    }
                                }
                            }
                        }
                    }]
                }]
            }
        }
    }

    article = _extract_tweet_article(
        payload,
        1879992969727537510,
        "https://x.com/i/article/1879975055066607616",
    )

    assert article == {
        "url": "https://x.com/i/article/1879975055066607616",
        "id": "1879975055066607616",
        "title": "Monitoring SJ-25",
        "preview_text": "Preview",
        "text": "Full article body",
    }


def test_extract_article_content_state_blocks_when_plain_text_is_absent():
    payload = {
        "tweet": {
            "rest_id": "1879992969727537510",
            "legacy": {},
            "article": {
                "article_results": {
                    "result": {
                        "title": "Monitoring SJ-25",
                        "content_state": {
                            "blocks": [
                                {"text": "First paragraph."},
                                {"text": "Second paragraph."},
                            ]
                        },
                    }
                }
            },
        }
    }

    article = _extract_tweet_article(payload, 1879992969727537510, None)

    assert article["text"] == "First paragraph.\n\nSecond paragraph."


def test_article_record_only_keeps_article_payload():
    tweet = _tweet()
    article = {
        "url": "https://x.com/i/article/1879975055066607616",
        "title": "Monitoring SJ-25",
        "text": "Full article body",
    }

    record = TwitterAdapter._tweet_record(tweet, article)

    assert record["article"] == article
    assert "text" not in record
    assert "tweet_text" not in record
    assert "links" not in record


def test_regular_tweet_keeps_top_level_text():
    record = TwitterAdapter._tweet_record(_tweet())

    assert record["text"] == "https://t.co/TYQOicKtay"
    assert "article" not in record


@pytest.mark.asyncio
async def test_article_proxy_transport_failure_keeps_proxy_rotation_policy():
    adapter = object.__new__(TwitterAdapter)
    adapter._api = SimpleNamespace(
        tweet_details_raw=AsyncMock(
            side_effect=_TwitterProxyRetryError("Twitter proxy transport failed")
        )
    )
    adapter._site_logger = Mock()

    with pytest.raises(_TwitterProxyRetryError):
        await adapter._fetch_article(
            1879992969727537510,
            "https://x.com/i/article/1879975055066607616",
        )


@pytest.mark.asyncio
async def test_article_parse_failure_does_not_drop_tweet():
    adapter = object.__new__(TwitterAdapter)
    adapter._api = SimpleNamespace(
        tweet_details_raw=AsyncMock(side_effect=ValueError("unsupported article"))
    )
    adapter._site_logger = Mock()

    article = await adapter._fetch_article(
        1879992969727537510,
        "https://x.com/i/article/1879975055066607616",
    )

    assert article == {}
    adapter._site_logger.warning.assert_called_once()
