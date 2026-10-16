import unittest
from unittest.mock import AsyncMock

from app.adapters.utils.news.wp.assets import enrich_cover_images_batch


class TestEmbeddedCoverImages(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = AsyncMock()
        self.client.request_page.return_value = (
            '{"source_url":"https://example.test/unwanted-media-request.jpg"}'
        )

    async def enrich(self, records):
        await enrich_cover_images_batch(
            self.client, "https://spacedaily.com", records, {}, "spacedaily_news"
        )
        self.client.request_page.assert_not_awaited()

    async def test_embedded_featured_media_supplies_cover_url_and_metadata(self):
        record = {
            "featured_media": 314997,
            "_embedded": {"wp:featuredmedia": [{
                "source_url": "https://example.test/cover.jpg",
                "mime_type": "image/jpeg",
                "media_details": {"width": 800, "height": 600},
            }]},
            "og_image": "https://example.test/other.jpg",
        }

        await self.enrich([record])

        self.assertEqual(record["featured_media"], {
            "source_url": "https://example.test/cover.jpg",
            "mime_type": "image/jpeg",
            "media_details": {"width": 800, "height": 600},
        })

    async def test_missing_or_unavailable_embedded_media_never_fetches_media_id(self):
        records = [
            {"featured_media": 314997},
            {"featured_media": 314998, "_embedded": {"wp:featuredmedia": []}},
            {"featured_media": 314999, "_embedded": {"wp:featuredmedia": [
                {"code": "rest_forbidden", "data": {"status": 401}},
            ]}},
        ]

        await self.enrich(records)

    async def test_existing_yoast_image_is_used_without_a_media_request(self):
        record = {
            "featured_media": 314997,
            "yoast_head_json": {"og_image": [{
                "url": "https://example.test/yoast.jpg", "width": 800,
            }]},
        }

        await self.enrich([record])

        self.assertEqual(record["featured_media"]["source_url"], "https://example.test/yoast.jpg")

    async def test_already_resolved_featured_media_is_preserved(self):
        media = {"source_url": "https://example.test/resolved.jpg"}
        record = {"featured_media": media.copy()}

        await self.enrich([record])

        self.assertEqual(record["featured_media"], media)


if __name__ == "__main__":
    unittest.main()
