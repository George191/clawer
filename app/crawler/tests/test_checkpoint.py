from app.crawler.checkpoint import PageCheckpointStore


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, **kwargs) -> None:
        self.values[key] = value

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)


async def test_page_checkpoint_is_kept_until_explicitly_cleared() -> None:
    redis = FakeRedis()
    store = PageCheckpointStore("google_patent", "query", redis_client=redis)

    assert await store.load() is None

    await store.save(4)
    assert await store.load() == 4

    await store.clear()
    assert await store.load() is None
