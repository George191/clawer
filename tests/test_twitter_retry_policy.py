from unittest.mock import AsyncMock

import pytest

from app.adapters import twitter


@pytest.mark.asyncio
async def test_proxy_failures_keep_rotating_after_old_limit():
    adapter = twitter.TwitterAdapter("https://x.com")
    for attempt in (0, 3, 25):
        action = await adapter.on_error(
            twitter._TwitterProxyRetryError("proxy failed"), 1, attempt
        )
        assert action == "rotate_proxy"


@pytest.mark.asyncio
async def test_other_failures_retry_with_backoff(monkeypatch):
    adapter = twitter.TwitterAdapter("https://x.com")
    sleep = AsyncMock()
    monkeypatch.setattr(twitter.asyncio, "sleep", sleep)
    monkeypatch.setattr(twitter.settings, "http_retry_backoff", 2.0)

    assert await adapter.on_error(ValueError("temporary failure"), 1, 25) is None
    sleep.assert_awaited_once_with(2.0)
