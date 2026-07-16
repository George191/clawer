"""HTTP 客户端 — 基于 curl_cffi 的 TLS 指纹请求库，支持流式下载。

功能：
- 浏览器指纹模拟（Chrome / Firefox 等）
- 自动重试 + 指数退避（tenacity）
- 流式下载（stream / download_bytes）支持大文件
- 文件大小限制和临时文件清理
- 代理支持和 Cookie 持久化
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from app.config.settings import settings
from app.models.template import RequestConfig

from curl_cffi import requests as curl_requests

logger = logging.getLogger(__name__)

# 反爬层（延迟导入避免循环依赖）
_proxy_pool = None
_delayer = None
_rotator = None

def _init_anti_crawl():
    """延迟初始化反爬各组件。"""
    global _proxy_pool, _delayer, _rotator
    if _rotator is None:
        from app.anti_crawl.request_delayer import get_delayer
        from app.anti_crawl.identity_rotator import get_identity_rotator
        _delayer = get_delayer()
        _rotator = get_identity_rotator()
    if settings.anti_crawl_enabled and _proxy_pool is None:
        from app.anti_crawl.proxy_pool import get_proxy_pool
        _proxy_pool = get_proxy_pool()


class DownloadError(Exception):
    def __init__(self, url: str, status_code: int | None = None, message: str = ""):
        self.url = url
        self.status_code = status_code
        super().__init__(f"Download failed: {url} (status={status_code}): {message}")


class FileTooLargeError(DownloadError):
    def __init__(self, url: str, size: int, max_size: int):
        self.size = size
        self.max_size = max_size
        super().__init__(url, message=f"File size {size} exceeds limit {max_size}")


class HttpClient:
    def __init__(self) -> None:
        self._last_proxy_url: str | None = None
        # 协程级代理分配：每个协程独立租用一个代理 IP
        self._leased_proxies: dict[int, str] = {}
        self._lease_lock: asyncio.Lock | None = None

    async def _get_lease_lock(self) -> asyncio.Lock:
        if self._lease_lock is None:
            self._lease_lock = asyncio.Lock()
        return self._lease_lock

    async def _create_client(
        self,
        proxy_url: str | None = None,
        no_timeout: bool = False,
    ) -> curl_requests.AsyncSession:
        session_kwargs: dict = {
            "impersonate": "chrome120",
            "proxy": proxy_url,
            "timeout": None if no_timeout else settings.http_request_timeout,
            "headers": {"User-Agent": settings.http_user_agent},
            "verify": settings.http_verify_ssl,
            "allow_redirects": True,
        }
        if settings.http_interface:
            session_kwargs["interface"] = settings.http_interface
            logger.debug("Binding to network interface: %s", settings.http_interface)
        return curl_requests.AsyncSession(**session_kwargs)

    async def request_page(
        self,
        url: str,
        config: RequestConfig | None = None,
        anti_crawl_enabled: bool | None = None,
        force_direct: bool = False,
        page: int = 0,
        attempt: int = 0,
        no_timeout: bool = False,
    ) -> str:
        """请求页面并返回文本内容。

        Args:
            url: 请求 URL。
            config: 请求配置。
            anti_crawl_enabled: 模板级反爬开关。None=使用全局配置, True/False=覆盖全局。
            force_direct: 强制绕过隧道代理和代理池，仅用于连接预检。
            page: 当前页码，用于日志记录。
            attempt: 当前尝试次数，用于日志记录。
            no_timeout: 不设置连接或响应超时，仅用于交互式网站预检。

        Returns:
            响应文本。
        """
        config = config or RequestConfig()

        headers = dict(config.headers)
        cookies = dict(config.cookies)

        # 解析最终反爬开关：模板 > 全局
        use_anti_crawl = (anti_crawl_enabled if anti_crawl_enabled is not None
                          else settings.anti_crawl_enabled)

        if use_anti_crawl:
            _init_anti_crawl()
        
        if _rotator is not None and use_anti_crawl:
            anti_headers = _rotator.get_headers(target_url=url)
            for k, v in anti_headers.items():
                headers.setdefault(k, v)
            anti_cookies = _rotator.get_cookies()
            if anti_cookies:
                for k, v in anti_cookies.items():
                    cookies.setdefault(k, v)
        if _delayer is not None and _delayer.enabled and use_anti_crawl:
            await _delayer.delay(url)

        # ── 代理选择：隧道代理 > 协程独立代理 > 代理池 ──────────
        proxy_url = None
        task_id = id(asyncio.current_task()) if asyncio.current_task() else 0

        if force_direct:
            proxy_url = None
        elif settings.tunnel_proxy_url:
            proxy_url = settings.tunnel_proxy_url
        elif _proxy_pool is not None and _proxy_pool.enabled and use_anti_crawl:
            lock = await self._get_lease_lock()
            async with lock:
                if task_id in self._leased_proxies:
                    proxy_url = self._leased_proxies[task_id]
                else:
                    proxy_url = await _proxy_pool.lease_proxy(task_id)
                    if proxy_url:
                        self._leased_proxies[task_id] = proxy_url

        self._last_proxy_url = proxy_url

        url_display = url if len(url) <= 150 else f"{url[:70]}...{url[-70:]}"

        # ── 每次请求创建新的 AsyncSession，确保每次请求都能获取新的出口IP ──────────
        async with await self._create_client(proxy_url, no_timeout=no_timeout) as client:
            try:
                request_kwargs = dict(
                    method=config.method,
                    url=url,
                    headers=headers,
                    params=config.params,
                    cookies=cookies,
                )
                if config.method.upper() == "POST":
                    request_kwargs["data"] = config.form_data

                response = await client.request(**request_kwargs)

                if config.encoding:
                    response.encoding = config.encoding

                if response.status_code in settings.http_retry_on_statuses:
                    raise DownloadError(url_display, response.status_code, "Retryable status code")

                response.raise_for_status()

                if _proxy_pool is not None and proxy_url:
                    await _proxy_pool.mark_success(proxy_url)

                logger.info(
                    "[Page %d attempt %d] %s %s | status_code=%d | anti_crawl=%s | task=%d",
                    page, attempt, config.method, url_display, response.status_code, use_anti_crawl, task_id
                )
                return response.text

            except curl_requests.errors.RequestsError as e:
                if _proxy_pool is not None and proxy_url:
                    await _proxy_pool.mark_failure(proxy_url)
                    await self._release_failed_proxy(task_id, proxy_url)
                raise
            except Exception as e:
                if _proxy_pool is not None and proxy_url:
                    await _proxy_pool.mark_failure(proxy_url)
                    await self._release_failed_proxy(task_id, proxy_url)
                raise

    async def _release_failed_proxy(self, task_id: int, proxy_url: str) -> None:
        """释放失效的协程代理并尝试获取新代理。"""
        lock = await self._get_lease_lock()
        async with lock:
            if self._leased_proxies.get(task_id) == proxy_url:
                del self._leased_proxies[task_id]
                # 尝试为该协程分配新代理
                if _proxy_pool is not None and _proxy_pool.enabled:
                    new_proxy = await _proxy_pool.lease_proxy(task_id)
                    if new_proxy:
                        self._leased_proxies[task_id] = new_proxy
                        logger.info(f"[{new_proxy}] Replaced failed proxy {proxy_url} for task {task_id}")
                        
    async def download_bytes(
        self,
        url: str,
        config: RequestConfig | None = None,
    ) -> bytes:
        config = config or RequestConfig()

        headers = dict(config.headers)
        cookies = dict(config.cookies)

        _init_anti_crawl()

        if _rotator is not None and _rotator.enabled:
            anti_headers = _rotator.get_headers(target_url=url)
            for k, v in anti_headers.items():
                headers.setdefault(k, v)
            anti_cookies = _rotator.get_cookies()
            if anti_cookies:
                for k, v in anti_cookies.items():
                    cookies.setdefault(k, v)

        if _delayer is not None and _delayer.enabled:
            await _delayer.delay(url)

        # ── 代理选择：隧道代理 > 代理池 ──────────────────────────
        proxy_url = None
        task_id = id(asyncio.current_task()) if asyncio.current_task() else 0

        if settings.tunnel_proxy_url:
            proxy_url = settings.tunnel_proxy_url
        elif _proxy_pool is not None and _proxy_pool.enabled:
            lock = await self._get_lease_lock()
            async with lock:
                if task_id in self._leased_proxies:
                    proxy_url = self._leased_proxies[task_id]
                else:
                    proxy_url = await _proxy_pool.lease_proxy(task_id)
                    if proxy_url:
                        self._leased_proxies[task_id] = proxy_url

        logger.info("Downloading bytes: %s with proxy: %s", url, proxy_url or "None")

        # ── 每次请求创建新的 AsyncSession，确保每次请求都能获取新的出口IP ──────────
        async with await self._create_client(proxy_url) as client:
            try:
                stream_kwargs = dict(
                    method=config.method or "GET",
                    url=url,
                    headers=headers,
                    params=config.params,
                    cookies=cookies,
                    timeout=settings.http_download_timeout,
                )

                async with client.stream(**stream_kwargs) as response:
                    if response.status_code in settings.http_retry_on_statuses:
                        raise DownloadError(url, response.status_code, "Retryable status code")

                    response.raise_for_status()

                    chunks: list[bytes] = []
                    total_size = 0
                    async for chunk in response.aiter_content(
                        chunk_size=settings.download_chunk_size
                    ):
                        total_size += len(chunk)
                        if total_size > settings.download_max_file_size:
                            raise FileTooLargeError(
                                url, total_size, settings.download_max_file_size
                            )
                        chunks.append(chunk)

                data = b"".join(chunks)
                logger.info("Download complete: %s (%d bytes)", url, total_size)

                if _proxy_pool is not None and proxy_url:
                    await _proxy_pool.mark_success(proxy_url)

                return data

            except Exception as e:
                if _proxy_pool is not None and proxy_url:
                    await _proxy_pool.mark_failure(proxy_url)
                    await self._release_failed_proxy(task_id, proxy_url)
                raise

    async def close(self) -> None:
        """关闭HTTP客户端资源。
        
        由于每次请求都创建新的AsyncSession并通过上下文管理器自动关闭，
        这里主要用于清理协程级代理租约。
        """
        self._leased_proxies.clear()
    
    async def mark_last_proxy_failed(self) -> None:
        """标记最后使用的代理为失败并释放协程租约，这样下次请求会使用新代理。"""
        if self._last_proxy_url and _proxy_pool is not None and _proxy_pool.enabled:
            await _proxy_pool.mark_failure(self._last_proxy_url)
            task_id = id(asyncio.current_task()) if asyncio.current_task() else 0
            await self._release_failed_proxy(task_id, self._last_proxy_url)
