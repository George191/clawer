import json
from datetime import datetime, timezone

from app.etl.normalizers.news import normalize_blacksky_press, normalize_ssc_news, normalize_ssc_press


def test_normalize_news_prefers_downloaded_asset_paths_for_media_fields() -> None:
    record = {
        "_meta": {
            "template": "blacksky_press",
            "data_source": "blacksky_press",
            "record_id": "news-1",
        },
        "title": "Example",
        "url": "https://example.com/news-1",
        "content_html": '<p><img src="{{img_0}}" alt=""></p>',
        "images": [
            {
                "url": "https://cdn.example.com/original.png",
                "placeholder": "{{img_0}}",
                "alt": "",
            }
        ],
        "assets": {
            "featured_media": {
                "source_url": "news/blacksky_press/news-1/cover.png",
            },
            "images": {
                "0": {
                    "url": "news/blacksky_press/news-1/body_00000.png",
                }
            },
        },
    }

    normalized = normalize_blacksky_press(record)

    images = json.loads(normalized["images"])
    assert images[0]["url"] == "news/blacksky_press/news-1/body_00000.png"
    assert normalized["thumbnail"] == "news/blacksky_press/news-1/cover.png"
    assert normalized["content_html"] == '<p><img src="news/blacksky_press/news-1/body_00000.png" alt=""></p>'
    assert "primary_attachment" not in normalized
    assert "extra_data" not in normalized


def test_normalize_news_prefers_downloaded_asset_paths_for_attachments_and_slides() -> None:
    record = {
        "_meta": {
            "template": "ssc_news",
            "data_source": "ssc_news",
            "record_id": "news-2",
        },
        "title": "Example 2",
        "url": "https://example.com/news-2",
        "slides": [
            {
                "url": "https://media.example.com/slide.jpg",
                "caption": "slide",
            }
        ],
        "attachments": [
            {
                "url": "https://files.example.com/report.pdf",
                "label": "report",
            }
        ],
        "assets": {
            "slides": {
                "0": {
                    "url": "news/ssc_news/news-2/slide_00000.jpg",
                }
            },
            "attachments": {
                "0": {
                    "url": "news/ssc_news/news-2/report.pdf",
                }
            },
        },
    }

    normalized = normalize_ssc_news(record)

    slides = json.loads(normalized["slides"])
    attachments = json.loads(normalized["attachments"])
    assert slides[0]["url"] == "news/ssc_news/news-2/slide_00000.jpg"
    assert attachments[0]["url"] == "news/ssc_news/news-2/report.pdf"
    assert "primary_attachment" not in normalized
    assert "extra_data" not in normalized


def test_normalize_ssc_news_parses_abbrev_month_with_dot() -> None:
    record = {
        "_meta": {
            "template": "ssc_news",
            "data_source": "ssc_news",
            "record_id": "00e6392a839e4c22b2ee891c1fab04e6",
        },
        "title": "SSC Example",
        "url": "https://example.com/ssc",
        "date": "Nov. 7, 2025",
        "modified": "Nov. 18, 2025",
    }

    normalized = normalize_ssc_news(record)

    assert normalized["source_published_at"] == datetime(2025, 11, 7, 0, 0, tzinfo=timezone.utc)
    assert normalized["source_updated_at"] == datetime(2025, 11, 18, 0, 0, tzinfo=timezone.utc)


def test_normalize_ssc_press_parses_abbrev_month_with_dot() -> None:
    record = {
        "_meta": {
            "template": "ssc_press",
            "data_source": "ssc_press",
            "record_id": "0b09f97918185900ad2fffa8f4388e4e",
        },
        "title": "SSC Press Example",
        "url": "https://example.com/ssc-press",
        "date": "Aug. 28, 2025",
        "modified": "Sep. 1, 2025",
    }

    normalized = normalize_ssc_press(record)

    assert normalized["source_published_at"] == datetime(2025, 8, 28, 0, 0, tzinfo=timezone.utc)
    assert normalized["source_updated_at"] == datetime(2025, 9, 1, 0, 0, tzinfo=timezone.utc)
