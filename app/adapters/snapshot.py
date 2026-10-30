"""Source-level snapshots for catalogs which expose one complete document."""

from app.crawler.source_snapshot import request_source_snapshot


class SingleSourceSnapshotMixin:
    """Skip parsing a committed, unchanged document; retain the source parser."""

    async def request_list_page(self, url, config, **kwargs):
        return await request_source_snapshot(
            self._client, self._crawl_context.get("snapshot_store"),
            url, config, **kwargs,
        )

    async def parse_list_response(self, page, content):
        if content is None:
            return []
        return await super().parse_list_response(page, content)
