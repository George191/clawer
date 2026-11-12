"""Task-local SOCKS transport for clients that accept only a single proxy URL."""

from __future__ import annotations

import asyncio
import socket
from contextlib import suppress
from urllib.parse import urlsplit

from curl_cffi import Curl, CurlInfo, CurlOpt

from app.downloader.browser_runtime import BrowserRuntimeError


class ProxyTunnel:
    """Forward Chromium TCP through an already selected front/exit proxy route."""

    def __init__(self, proxy_url: str, pre_proxy_url: str | None, *, timeout: float) -> None:
        self._check_route(proxy_url, pre_proxy_url)
        self._proxy_url = proxy_url
        self._pre_proxy_url = pre_proxy_url
        self._timeout = max(0.001, timeout)
        self._server: asyncio.Server | None = None
        self._connections: set[asyncio.Task] = set()
        self._connecting: set[asyncio.Task] = set()
        self._route_lock = asyncio.Lock()

    @staticmethod
    def _check_route(proxy_url: str, pre_proxy_url: str | None) -> None:
        scheme = urlsplit(proxy_url).scheme
        front_scheme = urlsplit(pre_proxy_url).scheme if pre_proxy_url else scheme
        if not proxy_url or front_scheme not in {"http", "socks5", "socks5h"}:
            raise BrowserRuntimeError("Browser collection requires an HTTP or SOCKS5 front proxy")
        if pre_proxy_url and (front_scheme not in {"socks5", "socks5h"} or scheme != "http"):
            raise BrowserRuntimeError("Browser proxy chaining requires a SOCKS5 front and HTTP exit")

    async def start(self) -> str:
        if self._server is None:
            self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        return f"socks5://127.0.0.1:{port}"

    async def update_route(self, proxy_url: str, pre_proxy_url: str | None) -> None:
        self._check_route(proxy_url, pre_proxy_url)
        async with self._route_lock:
            if (proxy_url, pre_proxy_url) == (self._proxy_url, self._pre_proxy_url):
                return
            await self._close_connections()
            self._proxy_url, self._pre_proxy_url = proxy_url, pre_proxy_url

    def _connect(self, host: str, port: int) -> tuple[Curl, socket.socket]:
        curl = Curl()
        try:
            authority = f"[{host}]" if ":" in host else host
            curl.setopt(CurlOpt.URL, f"http://{authority}:{port}/".encode())
            curl.setopt(CurlOpt.PROXY, self._proxy_url.encode())
            curl.setopt(CurlOpt.NOPROXY, b"")
            if self._pre_proxy_url:
                curl.setopt(CurlOpt.PRE_PROXY, self._pre_proxy_url.encode())
            curl.setopt(CurlOpt.HTTPPROXYTUNNEL, 1)
            curl.setopt(CurlOpt.CONNECT_ONLY, 1)
            curl.setopt(CurlOpt.TIMEOUT_MS, max(1, int(self._timeout * 1000)))
            curl.perform()
            descriptor = curl.getinfo(CurlInfo.ACTIVESOCKET)
            if descriptor < 0:
                raise OSError("Browser proxy connection has no active socket")
            borrowed = socket.socket(fileno=descriptor)
            try:
                connection = borrowed.dup()
            finally:
                borrowed.detach()
            connection.setblocking(False)
            return curl, connection
        except BaseException:
            curl.close()
            raise

    @staticmethod
    def _close_late_connection(task: asyncio.Task) -> None:
        if not task.cancelled() and task.exception() is None:
            curl, connection = task.result()
            connection.close()
            curl.close()

    @staticmethod
    async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
        if writer.can_write_eof():
            writer.write_eof()
            await writer.drain()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        owner = asyncio.current_task()
        self._connections.add(owner)
        curl = None
        connection = None
        upstream = None
        established = False
        pumps = []
        try:
            async with asyncio.timeout(self._timeout):
                version, count = await reader.readexactly(2)
                methods = await reader.readexactly(count)
                if version != 5 or 0 not in methods:
                    writer.write(b"\x05\xff")
                    await writer.drain()
                    return
                writer.write(b"\x05\x00")
                await writer.drain()
                version, command, _, address_type = await reader.readexactly(4)
                if version != 5 or command != 1:
                    raise ValueError("Browser proxy supports SOCKS5 CONNECT only")
                if address_type == 1:
                    host = socket.inet_ntop(socket.AF_INET, await reader.readexactly(4))
                elif address_type == 4:
                    host = socket.inet_ntop(socket.AF_INET6, await reader.readexactly(16))
                elif address_type == 3:
                    host = (await reader.readexactly((await reader.readexactly(1))[0])).decode("idna")
                else:
                    raise ValueError("Unsupported SOCKS5 address type")
                port = int.from_bytes(await reader.readexactly(2), "big")
                async with self._route_lock:
                    pending = asyncio.create_task(asyncio.to_thread(self._connect, host, port))
                    self._connecting.add(pending)
                    pending.add_done_callback(self._connecting.discard)
                try:
                    curl, connection = await asyncio.shield(pending)
                except asyncio.CancelledError:
                    pending.add_done_callback(self._close_late_connection)
                    raise
            upstream_reader, upstream = await asyncio.open_connection(sock=connection)
            writer.write(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
            await writer.drain()
            established = True
            pumps = [asyncio.create_task(self._pump(reader, upstream)),
                     asyncio.create_task(self._pump(upstream_reader, writer))]
            await asyncio.gather(*pumps)
        except asyncio.CancelledError:
            raise
        except Exception:
            if not established:
                with suppress(OSError):
                    writer.write(b"\x05\x04\x00\x01\x00\x00\x00\x00\x00\x00")
                    await writer.drain()
        finally:
            for task in pumps:
                task.cancel()
            if pumps:
                await asyncio.gather(*pumps, return_exceptions=True)
            for stream in (upstream, writer):
                if stream is not None:
                    stream.close()
                    with suppress(OSError):
                        await stream.wait_closed()
            if connection is not None:
                connection.close()
            if curl is not None:
                curl.close()
            self._connections.discard(owner)

    async def _close_connections(self) -> None:
        connections = list(self._connections)
        for task in connections:
            task.cancel()
        if connections:
            await asyncio.gather(*connections, return_exceptions=True)
        if self._connecting:
            await asyncio.gather(*self._connecting, return_exceptions=True)

    async def close(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.close()
        await self._close_connections()
        if server is not None:
            await server.wait_closed()
