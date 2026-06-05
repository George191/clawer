"""HTTP 客户端基础组件 — 重新导出 HttpClient 和 DownloadError。"""

from app.downloader.http_client import HttpClient, DownloadError

__all__ = ["HttpClient", "DownloadError"]