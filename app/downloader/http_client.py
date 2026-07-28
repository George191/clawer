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
import json
import logging
import socket
import sys
from urllib.parse import urlsplit, urlunsplit

from curl_cffi import CurlOpt, requests as curl_requests

from app.config.settings import settings
from app.logger import get_logger
from app.models.template import RequestConfig

logger = get_logger(__name__)

_proxy_debug_logger = logging.getLogger("app.downloader.proxy_debug")
if not _proxy_debug_logger.handlers:
    _proxy_debug_handler = logging.StreamHandler(sys.stdout)
    _proxy_debug_handler.setFormatter(
        logging.Formatter("%(asctime)s [PROXY DEBUG] %(message)s")
    )
    _proxy_debug_logger.addHandler(_proxy_debug_handler)
_proxy_debug_logger.setLevel(logging.DEBUG)
_proxy_debug_logger.propagate = False

# 反爬层（延迟导入避免循环依赖）
_proxy_pool = None
_delayer = None
_rotator = None

def _init_anti_crawl():
    """延迟初始化反爬各组件。"""
    global _proxy_pool, _delayer, _rotator
    if _rotator is None:
        from app.anti_crawl.identity_rotator import get_identity_rotator
        from app.anti_crawl.request_delayer import get_delayer
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
        # 按协程缓存最近使用的代理，避免并发模板互相标记对方的代理。
        self._last_proxy_urls: dict[int, str] = {}
        # 协程级代理分配：每个协程独立租用一个代理 IP
        self._leased_proxies: dict[int, str] = {}
        self._lease_lock: asyncio.Lock | None = None
        self._download_clients: dict[
            int,
            tuple[str | None, str | None, curl_requests.AsyncSession],
        ] = {}
        self._download_client_lock: asyncio.Lock | None = None

    @staticmethod
    def _error_status_code(error: Exception) -> int | None:
        status_code = getattr(error, "status_code", None)
        if status_code is not None:
            return status_code
        response = getattr(error, "response", None)
        return getattr(response, "status_code", None) if response is not None else None

    @classmethod
    def _should_mark_proxy_failure(
        cls,
        error: Exception,
        pre_proxy_url: str | None,
    ) -> bool:
        """Do not blame an exit proxy for an ambiguous jump-host failure."""
        return not (pre_proxy_url and cls._error_status_code(error) is None)

    @classmethod
    def _should_mark_download_proxy_failure(
        cls,
        error: Exception,
        pre_proxy_url: str | None = None,
    ) -> bool:
        """Only remove a proxy for errors that indicate the route failed.

        Resource-level failures (404/403) and local size validation do not say
        anything about the proxy, so retaining the lease avoids draining the
        shared pool on bad or expired asset URLs.
        """
        if isinstance(error, FileTooLargeError):
            return False
        if not cls._should_mark_proxy_failure(error, pre_proxy_url):
            return False
        status_code = cls._error_status_code(error)
        return status_code not in {403, 404}

    async def _get_lease_lock(self) -> asyncio.Lock:
        if self._lease_lock is None:
            self._lease_lock = asyncio.Lock()
        return self._lease_lock

    async def _get_download_client_lock(self) -> asyncio.Lock:
        if self._download_client_lock is None:
            self._download_client_lock = asyncio.Lock()
        return self._download_client_lock

    async def _get_download_client(
        self,
        task_id: int,
        proxy_url: str | None,
        pre_proxy_url: str | None,
    ) -> curl_requests.AsyncSession:
        lock = await self._get_download_client_lock()
        async with lock:
            cached = self._download_clients.get(task_id)
            if cached is not None:
                cached_proxy, cached_pre_proxy, client = cached
                if (cached_proxy, cached_pre_proxy) == (proxy_url, pre_proxy_url):
                    return client
                del self._download_clients[task_id]
                await client.close()

            client = await self._create_client(
                proxy_url,
                pre_proxy_url=pre_proxy_url,
            )
            self._download_clients[task_id] = (proxy_url, pre_proxy_url, client)
            return client

    async def _discard_download_client(self, task_id: int) -> None:
        lock = await self._get_download_client_lock()
        async with lock:
            cached = self._download_clients.pop(task_id, None)
        if cached is not None:
            await cached[2].close()

    async def _create_client(
        self,
        proxy_url: str | None = None,
        no_timeout: bool = False,
        pre_proxy_url: str | None = None,
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
        if proxy_url and pre_proxy_url:
            session_kwargs["curl_options"] = {CurlOpt.PRE_PROXY: pre_proxy_url}
        if settings.http_debug_proxy_ip:
            _proxy_debug_logger.debug(
                "session proxy=%s pre_proxy(jump)=%s interface=%s",
                self._safe_proxy_url(proxy_url),
                self._safe_proxy_url(pre_proxy_url),
                settings.http_interface or "default",
            )
        return curl_requests.AsyncSession(**session_kwargs)

    @staticmethod
    def _safe_proxy_url(proxy_url: str | None) -> str:
        if not proxy_url:
            return "DIRECT"
        try:
            parsed = urlsplit(proxy_url)
            host = parsed.hostname or "unknown"
            port = f":{parsed.port}" if parsed.port else ""
            return urlunsplit((parsed.scheme, f"{host}{port}", parsed.path, "", ""))
        except ValueError:
            return "<invalid-proxy-url>"

    @staticmethod
    async def _resolve_proxy_host(proxy_url: str | None) -> str:
        if not proxy_url:
            return "DIRECT"
        try:
            host = urlsplit(proxy_url).hostname
            if not host:
                return "unknown"
            return await asyncio.to_thread(socket.gethostbyname, host)
        except (OSError, ValueError):
            return "unresolved"

    @staticmethod
    def _extract_exit_ip(payload: object) -> str:
        if isinstance(payload, dict):
            for key in ("origin", "ip", "query", "address"):
                value = payload.get(key)
                if value:
                    return str(value)
        if isinstance(payload, str):
            text = payload.strip()
            if text:
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    return text
                return HttpClient._extract_exit_ip(parsed)
        return "unknown"

    async def _probe_proxy_exit_ip(
        self,
        client: curl_requests.AsyncSession,
        proxy_url: str | None,
        pre_proxy_url: str | None,
        url_display: str,
        page: int,
        attempt: int,
    ) -> str:
        health_url = settings.proxy_health_check_url
        proxy_host_ip = await self._resolve_proxy_host(proxy_url)
        jump_host_ip = await self._resolve_proxy_host(pre_proxy_url)
        _proxy_debug_logger.debug(
            "proxy route target=%s page=%d attempt=%d jump_ip=%s proxy_ip=%s",
            self._safe_proxy_url(url_display),
            page,
            attempt,
            jump_host_ip,
            proxy_host_ip,
        )
        response = await client.get(health_url)
        response.raise_for_status()
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError):
            payload = response.text
        exit_ip = self._extract_exit_ip(payload)
        _proxy_debug_logger.debug(
            "proxy exit test target=%s status=%d exit_ip=%s jump_ip=%s proxy_ip=%s",
            self._safe_proxy_url(url_display),
            response.status_code,
            exit_ip,
            jump_host_ip,
            proxy_host_ip,
        )
        return exit_ip

    async def _before_request(
        self,
        client: curl_requests.AsyncSession,
        proxy_url: str | None,
        pre_proxy_url: str | None,
        url_display: str,
        page: int,
        attempt: int,
        method: str,
        use_anti_crawl: bool,
        task_id: int,
    ) -> str:
        """请求前处理：检测代理出口IP并记录日志。

        Args:
            client: AsyncSession 实例。
            proxy_url: 代理URL。
            pre_proxy_url: 跳板机代理URL。
            url_display: 显示用的URL。
            page: 当前页码。
            attempt: 当前尝试次数。
            method: 请求方法。
            use_anti_crawl: 是否启用反爬。
            task_id: 任务ID。

        Returns:
            代理出口信息字符串。
        """
        tunnel_info = self._safe_proxy_url(proxy_url)
        if settings.http_debug_proxy_ip:
            try:
                exit_ip = await self._probe_proxy_exit_ip(
                    client=client,
                    proxy_url=proxy_url,
                    pre_proxy_url=pre_proxy_url,
                    url_display=url_display,
                    page=page,
                    attempt=attempt,
                )
                tunnel_info = f"{self._safe_proxy_url(proxy_url)} exit_ip={exit_ip}"
            except Exception as e:
                _proxy_debug_logger.debug(
                    "proxy exit test failed target=%s proxy=%s pre_proxy=%s error=%s",
                    self._safe_proxy_url(url_display),
                    self._safe_proxy_url(proxy_url),
                    self._safe_proxy_url(pre_proxy_url),
                    e,
                )
                tunnel_info = f"{self._safe_proxy_url(proxy_url)} exit_ip=unavailable"

        return tunnel_info

    async def _after_request(
        self,
        response: curl_requests.Response,
        proxy_url: str | None,
        url_display: str,
        page: int,
        attempt: int,
        method: str,
        tunnel_info: str,
        use_anti_crawl: bool,
        task_id: int,
    ) -> None:
        """请求后处理：记录响应日志并标记代理状态。

        Args:
            response: HTTP响应对象。
            proxy_url: 代理URL。
            url_display: 显示用的URL。
            page: 当前页码。
            attempt: 当前尝试次数。
            method: 请求方法。
            tunnel_info: 代理出口信息。
            use_anti_crawl: 是否启用反爬。
            task_id: 任务ID。
        """
        logger.info(
            "[Page %d attempt %d] %s %s | tunnel=%s | status_code=%d | anti_crawl=%s | task=%d",
            page, attempt, method, url_display, tunnel_info, response.status_code, use_anti_crawl, task_id
        )

        if _proxy_pool is not None and proxy_url:
            await _proxy_pool.mark_success(proxy_url)

    async def request_page(
        self,
        url: str,
        config: RequestConfig | None = None,
        anti_crawl_enabled: bool | None = None,
        force_direct: bool = False,
        page: int = 0,
        attempt: int = 0,
        no_timeout: bool = False,
        adapter_name: str | None = None,
    ) -> str:
        """请求页面并返回文本内容。

        Args:
            url: 请求 URL。
            config: 请求配置。
            anti_crawl_enabled: 模板级反爬开关。None=使用全局配置, True/False=覆盖全局。
            adapter_name: 当前模板适配器名称，用于隔离代理失败记录。
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
        pre_proxy_url = None
        task_id = id(asyncio.current_task()) if asyncio.current_task() else 0

        if force_direct:
            proxy_url = None
        elif settings.tunnel_proxy_url:
            proxy_url = settings.tunnel_proxy_url
        elif _proxy_pool is not None and _proxy_pool.enabled and use_anti_crawl:
            pre_proxy_url = settings.proxy_pre_proxy_url or None
            lock = await self._get_lease_lock()
            async with lock:
                if task_id in self._leased_proxies:
                    proxy_url = self._leased_proxies[task_id]
                else:
                    proxy_url = await _proxy_pool.lease_proxy(task_id, adapter_name)
                    if proxy_url:
                        self._leased_proxies[task_id] = proxy_url

        if proxy_url:
            self._last_proxy_urls[task_id] = proxy_url
        else:
            self._last_proxy_urls.pop(task_id, None)

        url_display = url if len(url) <= 150 else f"{url[:70]}...{url[-70:]}"

        # ── 每次请求创建新的 AsyncSession，确保每次请求都能获取新的出口IP ──────────
        async with await self._create_client(
            proxy_url,
            no_timeout=no_timeout,
            pre_proxy_url=pre_proxy_url,
        ) as client:
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
        
                tunnel_info = await self._before_request(
                    client=client,
                    proxy_url=proxy_url,
                    pre_proxy_url=pre_proxy_url,
                    url_display=url_display,
                    page=page,
                    attempt=attempt,
                    method=config.method,
                    use_anti_crawl=use_anti_crawl,
                    task_id=task_id,
                )
                
                response = await client.request(**request_kwargs)

                if config.encoding:
                    response.encoding = config.encoding

                if response.status_code in settings.http_retry_on_statuses:
                    raise DownloadError(url_display, response.status_code, "Retryable status code")

                response.raise_for_status()

                await self._after_request(
                    response=response,
                    proxy_url=proxy_url,
                    url_display=url_display,
                    page=page,
                    attempt=attempt,
                    method=config.method,
                    tunnel_info=tunnel_info,
                    use_anti_crawl=use_anti_crawl,
                    task_id=task_id,
                )

                return response.text

            except Exception as e:
                if (
                    _proxy_pool is not None
                    and proxy_url
                    and self._should_mark_proxy_failure(e, pre_proxy_url)
                ):
                    await _proxy_pool.mark_failure(proxy_url, adapter_name)
                    await self._release_failed_proxy(task_id, proxy_url, adapter_name)
                raise

    async def _release_failed_proxy(
        self,
        task_id: int,
        proxy_url: str,
        adapter_name: str | None = None,
    ) -> None:
        """释放失效的协程代理并尝试获取新代理。"""
        lock = await self._get_lease_lock()
        async with lock:
            if self._leased_proxies.get(task_id) == proxy_url:
                del self._leased_proxies[task_id]
                if self._last_proxy_urls.get(task_id) == proxy_url:
                    del self._last_proxy_urls[task_id]
                await _proxy_pool.release_proxy(task_id)
                # 尝试为该协程分配新代理
                if _proxy_pool is not None and _proxy_pool.enabled:
                    new_proxy = await _proxy_pool.lease_proxy(task_id, adapter_name)
                    if new_proxy:
                        self._leased_proxies[task_id] = new_proxy
                        logger.debug(f"[{new_proxy}] Replaced failed proxy {proxy_url} for task {task_id}")
                        
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
        pre_proxy_url = None
        task_id = id(asyncio.current_task()) if asyncio.current_task() else 0

        if settings.tunnel_proxy_url:
            proxy_url = settings.tunnel_proxy_url
        elif _proxy_pool is not None and _proxy_pool.enabled:
            pre_proxy_url = settings.proxy_pre_proxy_url or None
            lock = await self._get_lease_lock()
            async with lock:
                if task_id in self._leased_proxies:
                    proxy_url = self._leased_proxies[task_id]
                else:
                    proxy_url = await _proxy_pool.lease_proxy(task_id)
                    if proxy_url:
                        self._leased_proxies[task_id] = proxy_url

        if proxy_url:
            self._last_proxy_urls[task_id] = proxy_url
        else:
            self._last_proxy_urls.pop(task_id, None)
            if _proxy_pool is not None and _proxy_pool.enabled:
                raise DownloadError(url, message="Download proxy unavailable")
        logger.debug(
            "Downloading bytes: %s with proxy: %s",
            url,
            self._safe_proxy_url(proxy_url),
        )

        client = await self._get_download_client(
            task_id,
            proxy_url,
            pre_proxy_url=pre_proxy_url,
        )
        try:
            stream_kwargs = dict(
                method=config.method or "GET",
                url=url,
                headers=headers,
                params=config.params,
                cookies=cookies,
                timeout=settings.http_download_timeout,
            )

            # download_bytes() returns the complete payload.  Using
            # AsyncSession.stream() here triggers curl_cffi's background
            # perform task; malformed proxy headers then escape as
            # "Task exception was never retrieved".  A non-stream request
            # keeps parsing and cleanup in the caller task.
            stream_kwargs["stream"] = False
            response = await client.request(**stream_kwargs)
            if response.status_code in settings.http_retry_on_statuses:
                raise DownloadError(url, response.status_code, "Retryable status code")

            response.raise_for_status()
            data = response.content
            total_size = len(data)
            if total_size > settings.download_max_file_size:
                raise FileTooLargeError(
                    url, total_size, settings.download_max_file_size
                )
            logger.debug("Download complete: %s (%d bytes)", url, total_size)

            if _proxy_pool is not None and proxy_url:
                await _proxy_pool.mark_success(proxy_url)

            return data

        except Exception as e:
            proxy_failed = (
                _proxy_pool is not None
                and proxy_url
                and self._should_mark_download_proxy_failure(e, pre_proxy_url)
            )
            transport_failed = (
                self._error_status_code(e) is None
                and not isinstance(e, FileTooLargeError)
            )
            if proxy_failed or transport_failed:
                await self._discard_download_client(task_id)
            if proxy_failed:
                await _proxy_pool.mark_failure(proxy_url)
                await self._release_failed_proxy(task_id, proxy_url)
            raise

    async def close(self) -> None:
        """关闭HTTP客户端资源。
        
        关闭按资源 worker 复用的下载会话，并清理协程级代理租约。
        """
        clients = [cached[2] for cached in self._download_clients.values()]
        self._download_clients.clear()
        if clients:
            await asyncio.gather(
                *(client.close() for client in clients),
                return_exceptions=True,
            )
        if _proxy_pool is not None:
            for task_id in list(self._leased_proxies):
                await _proxy_pool.release_proxy(task_id)
        self._leased_proxies.clear()
        self._last_proxy_urls.clear()

    async def release_current_task_proxy(self) -> None:
        """Release the proxy lease owned by the current download task."""
        task = asyncio.current_task()
        task_id = id(task) if task else 0
        await self._discard_download_client(task_id)
        lock = await self._get_lease_lock()
        async with lock:
            self._leased_proxies.pop(task_id, None)
            self._last_proxy_urls.pop(task_id, None)
            if _proxy_pool is not None:
                await _proxy_pool.release_proxy(task_id)

    async def mark_last_proxy_failed(self, adapter_name: str | None = None) -> None:
        """标记最后使用的代理为失败并释放协程租约，这样下次请求会使用新代理。"""
        task_id = id(asyncio.current_task()) if asyncio.current_task() else 0
        proxy_url = self._last_proxy_urls.get(task_id)
        if proxy_url and _proxy_pool is not None and _proxy_pool.enabled:
            await _proxy_pool.mark_failure(proxy_url, adapter_name)
            await self._release_failed_proxy(
                task_id,
                proxy_url,
                adapter_name,
            )
