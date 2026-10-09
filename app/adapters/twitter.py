"""Collect user timelines through twscrape's authenticated account pool."""

from __future__ import annotations

import asyncio
import json
import re
from contextlib import aclosing, contextmanager
from contextvars import ContextVar
from datetime import timezone
from pathlib import Path
from typing import Any

from app.adapters import BaseSiteAdapter, register_adapter
from app.anti_crawl.proxy_pool import get_proxy_pool
from app.config.settings import settings
from app.utils.runtime_control import check_control_state

_ARTICLE_URL_PATTERN = re.compile(
    r"^https?://(?:www\.)?(?:x|twitter)\.com/(?:i/)?article/\d+(?:[/?#]|$)",
    re.IGNORECASE,
)


class _TwitterProxyRetryError(RuntimeError):
    """Release the current account and retry through another proxy."""


@contextmanager
def _twitter_transport(pre_proxy: str | None, rotate_on_network: bool = False):
    """Apply the project's proxy-chain setting to twscrape's HTTP clients."""
    from curl_cffi import CurlOpt
    from twscrape import account, http, queue_client, xclid

    context = getattr(http, "_spider_transport_context", None)
    if context is None:
        context = ContextVar("twitter_transport", default=None)
        http._spider_transport_context = context

    # MinIO reloads adapters in persistent workers. An older adapter may have
    # installed the transport context without the immediate-retry hooks.
    if getattr(http, "_spider_transport_version", None) != 2:
        original_xclid_get = queue_client.XClIdGenStore.get
        original_ctx_retry = queue_client.Ctx.retry

        def wrap(original):
            def make_client(*args, **kwargs):
                options = context.get()
                if options is None:
                    return original(*args, **kwargs)
                client = http.make_client(*args, **{**kwargs, "backend": "curl"})
                client._session.timeout = settings.http_request_timeout
                client._session.verify = settings.http_verify_ssl
                if settings.http_interface:
                    client._session.interface = settings.http_interface
                if options["pre_proxy"] and kwargs.get("proxy"):
                    client._session.curl_options[CurlOpt.PRE_PROXY] = options["pre_proxy"]
                if options.get("rotate_on_network"):
                    # CurlClient otherwise retries a timeout four times before
                    # QueueClient sees it. Let the first failure reach the queue.
                    async def request_once(method, url, **request_kwargs):
                        return await client._wrap(
                            client._session.request(method, url, **request_kwargs)
                        )

                    client.request = request_once
                return client

            return make_client

        account._make_http_client = wrap(account._make_http_client)
        xclid._make_http_client = wrap(xclid._make_http_client)

        async def get_xclid(cls, *args, **kwargs):
            try:
                return await original_xclid_get(*args, **kwargs)
            except xclid.XClIdParseError:
                options = context.get()
                if options is not None:
                    options["xclid_error"] = True
                raise

        queue_client.XClIdGenStore.get = classmethod(get_xclid)

        async def retry_transport(ctx, kind):
            options = context.get()
            if (
                options is not None
                and options.get("rotate_on_network")
                and getattr(kind, "name", None) == "TRANSPORT"
            ):
                # Called from QueueClient's network-error handler. Raising here
                # skips its cooldown; __aexit__ releases only this account lease.
                raise _TwitterProxyRetryError("Twitter proxy transport failed")
            return await original_ctx_retry(ctx, kind)

        queue_client.Ctx.retry = retry_transport
        http._spider_transport_version = 2

    state = {
        "pre_proxy": pre_proxy,
        "xclid_error": False,
        "rotate_on_network": rotate_on_network,
    }
    token = context.set(state)
    try:
        yield state
    finally:
        context.reset(token)


@register_adapter("twitter")
class TwitterAdapter(BaseSiteAdapter):
    adapter_name = "twitter"

    async def on_before_crawl(self, template: Any) -> None:
        await super().on_before_crawl(template)
        params = template._param_values
        self._username = params.get("username", "").strip()
        if self._username:
            self._username = self.build_batch_param_value([self._username], "username")
            self._user_id = None
            template.list_page = f"/{self._username}"
        else:
            self._user_id = int(self.build_batch_param_value([params.get("id", "")], "id"))
        self._limit = int(params.get("limit", "0"))
        if self._limit < 0:
            raise ValueError("Twitter limit must be a non-negative integer (0 means unlimited)")

        accounts_db = Path(params.get("accounts_db", "data/twitter_accounts.db"))
        if not accounts_db.is_file():
            raise ValueError(
                "Twitter accounts_db is missing. Import an X session with "
                "twscrape --db <accounts_db> add_cookie <account_name> first."
            )

        # Keep unrelated adapters loadable when the crawler extra is not installed.
        from twscrape import API

        self._explicit_proxy = params.get("proxy", "").strip() or None
        self._api = API(
            str(accounts_db),
            proxy=self._explicit_proxy,
            raise_when_no_account=True,
        )

    async def request_list_page(self, url: str, config: Any, **kwargs: Any) -> str:
        await check_control_state()
        proxy = self._explicit_proxy
        pre_proxy = None
        pool = None
        lease_id = id(asyncio.current_task())
        source = "template" if proxy else "account/environment"
        use_anti_crawl = kwargs.get("anti_crawl_enabled")
        if use_anti_crawl is None:
            use_anti_crawl = settings.anti_crawl_enabled

        try:
            # Match the project's proxy priority: explicit template proxy, then
            # tunnel, then the shared anti-crawl proxy pool, then twscrape's
            # account/environment proxy.
            if not proxy and settings.tunnel_proxy_url:
                proxy, source = settings.tunnel_proxy_url, "tunnel"
            if not proxy and use_anti_crawl:
                candidate = get_proxy_pool()
                if candidate.enabled:
                    pool = candidate
                    proxy = await pool.lease_proxy(lease_id, self.adapter_name)
                    if proxy:
                        source = "pool"
                        pre_proxy = settings.proxy_pre_proxy_url or None
                    else:
                        raise RuntimeError("Twitter proxy pool has no available proxy")
            if not proxy and settings.http_proxy:
                proxy, source = settings.http_proxy, "http_proxy"

            self._api.proxy = proxy
            self._site_logger.info(
                "Twitter network: source=%s proxy=%s pre_proxy=%s",
                source,
                bool(proxy),
                bool(pre_proxy),
            )
            with _twitter_transport(pre_proxy, rotate_on_network=pool is not None) as transport:
                return await self._collect_timeline(transport)
        except Exception as exc:
            # XClId parsing happens before the GraphQL request. A proxy can
            # return a challenge/HTML page with status 200, so twscrape
            # reports a parse error instead of a transport error.
            if (
                pool is not None
                and proxy
                and type(exc).__name__ in {
                    "XClIdParseError",
                    "NetworkError",
                    "ConnectError",
                    "_TwitterProxyRetryError",
                }
            ):
                await pool.mark_failure(proxy, self.adapter_name)
            raise
        finally:
            self._api.proxy = self._explicit_proxy
            if pool is not None:
                await pool.release_proxy(lease_id)

    async def _collect_timeline(self, transport: Any) -> str:
        await check_control_state()
        state = transport
        if self._user_id is None:
            user = await self._api.user_by_login(self._username)
            if user is None:
                if state.get("xclid_error"):
                    from twscrape.xclid import XClIdParseError

                    raise XClIdParseError("X web client transaction ID could not be generated")
                raise ValueError(f"Twitter user could not be resolved: {self._username}")
            self._user_id = user.id
        from twscrape.models import parse_tweets

        records: list[dict[str, Any]] = []
        seen: set[int] = set()
        async with aclosing(
            self._api.user_tweets_and_replies_raw(
                self._user_id,
                limit=self._limit or -1,
            )
        ) as pages:
            async for response in pages:
                payload = response.json()
                remaining = self._limit - len(records) if self._limit else -1
                for tweet in parse_tweets(payload, remaining):
                    await check_control_state()
                    if tweet.id in seen:
                        continue
                    seen.add(tweet.id)
                    article_url = self._article_url(tweet)
                    article = _extract_tweet_article(payload, tweet.id, article_url)
                    if article_url:
                        detail_article = await self._fetch_article(tweet.id, article_url)
                        article = {**article, **detail_article}
                    records.append(self._tweet_record(tweet, article or None))
                    if self._limit and len(records) >= self._limit:
                        break
                if self._limit and len(records) >= self._limit:
                    break
        if state.get("xclid_error"):
            from twscrape.xclid import XClIdParseError

            raise XClIdParseError("X web client transaction ID could not be generated")
        return json.dumps({"data": records}, ensure_ascii=False)

    async def _fetch_article(self, tweet_id: int, article_url: str) -> dict[str, Any]:
        try:
            response = await self._api.tweet_details_raw(tweet_id)
            if response is None:
                return {}
            return _extract_tweet_article(response.json(), tweet_id, article_url)
        except _TwitterProxyRetryError:
            raise
        except Exception as exc:
            self._site_logger.warning(
                "Twitter Article detail unavailable for tweet %s: %s",
                tweet_id,
                exc,
            )
            return {}

    @staticmethod
    def _article_url(tweet: Any) -> str | None:
        for link in getattr(tweet, "links", []) or []:
            url = str(getattr(link, "url", "") or "").strip()
            if _ARTICLE_URL_PATTERN.match(url):
                return url
        return None

    @staticmethod
    def _tweet_record(
        tweet: Any,
        article: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        metrics = {
            "retweet_count": tweet.retweetCount,
            "reply_count": tweet.replyCount,
            "like_count": tweet.likeCount,
            "quote_count": tweet.quoteCount,
            "bookmark_count": tweet.bookmarkedCount,
        }
        if tweet.viewCount is not None:
            metrics["impression_count"] = tweet.viewCount
        references = []
        if tweet.inReplyToTweetId is not None:
            references.append({"type": "replied_to", "id": str(tweet.inReplyToTweetId)})
        for relation, related in (
            ("retweeted", tweet.retweetedTweet), ("quoted", tweet.quotedTweet)
        ):
            if related is not None:
                references.append({"type": relation, "id": str(related.id)})
        record = {
            "id": str(tweet.id),
            "url": tweet.url,
            "author_id": str(tweet.user.id),
            "conversation_id": str(tweet.conversationId),
            "created_at": tweet.date.astimezone(timezone.utc).isoformat(
                timespec="milliseconds"
            ).replace("+00:00", "Z"),
            "lang": tweet.lang,
            "edit_history_tweet_ids": (tweet.editControl or {}).get("edit_tweet_ids"),
            "in_reply_to_user_id": (
                str(tweet.inReplyToUser.id) if tweet.inReplyToUser else None
            ),
            "possibly_sensitive": tweet.possibly_sensitive,
            "public_metrics": metrics,
            "referenced_tweets": references,
        }
        if article:
            record["article"] = article
        else:
            record["text"] = tweet.rawContent
        return record

    async def on_error(self, error: Exception, page: int, attempt: int) -> str | None:
        await check_control_state()
        error_type = type(error).__name__
        if error_type in {"XClIdParseError", "_TwitterProxyRetryError"}:
            reason = (
                "XClId parsing failed"
                if error_type == "XClIdParseError"
                else "Twitter proxy request failed"
            )
            self._site_logger.warning(
                "%s; rotating proxy (attempt %d): %s",
                reason,
                attempt + 1,
                error,
            )
            return "rotate_proxy"
        self._site_logger.warning(
            "Twitter request failed; retrying (attempt %d): %s",
            attempt + 1,
            error,
        )
        await asyncio.sleep(max(0.1, settings.http_retry_backoff))
        return None

    @classmethod
    def build_batch_param_value(cls, batch_data: list[str], param_name: str) -> str:
        if len(batch_data) != 1:
            raise ValueError("Twitter requires batch_size=1 (one account per batch)")
        user_id = batch_data[0].strip()
        if param_name == "username":
            username = user_id.removeprefix("@")
            if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", username):
                raise ValueError("Twitter username must contain 1-15 letters, digits or underscores")
            return username
        if not user_id.isascii() or not user_id.isdecimal() or int(user_id) < 1:
            raise ValueError("Twitter requires a username or a positive numeric user ID")
        return user_id


def _extract_tweet_article(
    payload: Any,
    tweet_id: int,
    article_url: str | None,
) -> dict[str, Any]:
    tweet_node = _find_tweet_node(payload, str(tweet_id))
    if tweet_node is None:
        return {"url": article_url} if article_url else {}

    article_node = _find_article_result(tweet_node)
    if article_node is None:
        return {"url": article_url} if article_url else {}

    article_id = article_node.get("rest_id") or article_node.get("id")
    title = _first_text(article_node, "title")
    preview_text = _first_text(article_node, "preview_text")
    plain_text = _first_text(article_node, "plain_text")
    content_text = _content_state_text(article_node.get("content_state"))
    text = plain_text or content_text or preview_text or title

    article: dict[str, Any] = {"url": article_url}
    if article_id:
        article["id"] = str(article_id)
    if title:
        article["title"] = title
    if preview_text:
        article["preview_text"] = preview_text
    if text:
        article["text"] = text
    return {key: value for key, value in article.items() if value is not None}


def _find_tweet_node(value: Any, tweet_id: str) -> dict[str, Any] | None:
    if isinstance(value, dict):
        if str(value.get("rest_id") or "") == tweet_id and (
            "legacy" in value or "article" in value
        ):
            return value
        for child in value.values():
            match = _find_tweet_node(child, tweet_id)
            if match is not None:
                return match
    elif isinstance(value, list):
        for child in value:
            match = _find_tweet_node(child, tweet_id)
            if match is not None:
                return match
    return None


def _find_article_result(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        article_results = value.get("article_results")
        if isinstance(article_results, dict):
            result = article_results.get("result")
            if isinstance(result, dict):
                return result
        for child in value.values():
            match = _find_article_result(child)
            if match is not None:
                return match
    elif isinstance(value, list):
        for child in value:
            match = _find_article_result(child)
            if match is not None:
                return match
    return None


def _first_text(value: Any, key: str) -> str | None:
    if isinstance(value, dict):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
        for child in value.values():
            match = _first_text(child, key)
            if match:
                return match
    elif isinstance(value, list):
        for child in value:
            match = _first_text(child, key)
            if match:
                return match
    return None


def _content_state_text(content_state: Any) -> str | None:
    if isinstance(content_state, str):
        try:
            content_state = json.loads(content_state)
        except json.JSONDecodeError:
            return content_state.strip() or None
    if not isinstance(content_state, dict):
        return None

    blocks = content_state.get("blocks")
    if isinstance(blocks, list):
        parts = [
            str(block.get("text") or "").strip()
            for block in blocks
            if isinstance(block, dict) and str(block.get("text") or "").strip()
        ]
        if parts:
            return "\n\n".join(parts)

    for child in content_state.values():
        text = _content_state_text(child)
        if text:
            return text
    return None
