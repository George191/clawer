"""Unbounded browser proxy handshakes remain cancellable."""

import asyncio
import threading
import unittest
from unittest.mock import Mock, patch

from curl_cffi import CurlOpt

from app.downloader import proxy_tunnel as module


class ProxyTunnelTests(unittest.IsolatedAsyncioTestCase):
    def test_unbounded_connect_disables_curl_timeouts(self):
        tunnel = module.ProxyTunnel("http://127.0.0.1:9999", None, timeout=None)
        curl = Mock()
        curl.perform.side_effect = OSError("fixture connection failure")
        with patch.object(module, "Curl", return_value=curl), patch.object(
            module, "configure_curl_cancellation", return_value=object(),
        ):
            with self.assertRaises(OSError):
                tunnel._connect("fixture.invalid", 443, threading.Event())
        curl.setopt.assert_any_call(CurlOpt.TIMEOUT_MS, 0)
        curl.setopt.assert_any_call(CurlOpt.CONNECTTIMEOUT_MS, 0)
        curl.close.assert_called_once()

    async def test_close_aborts_stalled_upstream_connect(self):
        received = asyncio.Event()
        release = asyncio.Event()

        async def stalled_proxy(reader, writer):
            try:
                await reader.readuntil(b"\r\n\r\n")
                received.set()
                await release.wait()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(stalled_proxy, "127.0.0.1", 0)
        self.addAsyncCleanup(server.wait_closed)
        self.addCleanup(server.close)
        port = server.sockets[0].getsockname()[1]
        tunnel = module.ProxyTunnel(f"http://127.0.0.1:{port}", None, timeout=None)
        self.addAsyncCleanup(tunnel.close)
        proxy = await tunnel.start()
        proxy_port = int(proxy.rsplit(":", 1)[1])
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
        try:
            writer.write(b"\x05\x01\x00")
            await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.readexactly(2), 1), b"\x05\x00")
            host = b"fixture.invalid"
            writer.write(b"\x05\x01\x00\x03" + bytes([len(host)]) + host + b"\x01\xbb")
            await writer.drain()
            await asyncio.wait_for(received.wait(), 2)
            self.assertTrue(tunnel._connecting)
            await asyncio.wait_for(tunnel.close(), 3)
            self.assertFalse(tunnel._connections)
            self.assertFalse(tunnel._connecting)
            self.assertEqual(await reader.read(), b"")
        finally:
            release.set()
            writer.close()
            await writer.wait_closed()

    async def test_finite_web_preview_handshake_timeout_is_preserved(self):
        tunnel = module.ProxyTunnel("http://127.0.0.1:9999", None, timeout=0.02)
        self.addAsyncCleanup(tunnel.close)
        proxy = await tunnel.start()
        reader, writer = await asyncio.open_connection("127.0.0.1", int(proxy.rsplit(":", 1)[1]))
        try:
            self.assertEqual((await asyncio.wait_for(reader.read(), 1))[:2], b"\x05\x04")
        finally:
            writer.close()
            await writer.wait_closed()
