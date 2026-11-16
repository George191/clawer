"""Cooperative cancellation for synchronous libcurl transfers."""

from threading import Event

from curl_cffi import Curl, CurlOpt
from curl_cffi._wrapper import ffi, lib


def configure_curl_cancellation(curl: Curl, stopped: Event):
    """Keep a transfer interruptible while it waits for network data."""
    @ffi.callback("int(void *, int64_t, int64_t, int64_t, int64_t)")
    def progress(_client, _download_total, _download_now, _upload_total, _upload_now):
        return int(stopped.is_set())

    result = lib._curl_easy_setopt(curl._curl, CurlOpt.XFERINFOFUNCTION, progress)
    curl._check_error(result, "setopt", CurlOpt.XFERINFOFUNCTION, progress)
    curl.setopt(CurlOpt.NOPROGRESS, 0)
    curl._spider_cancel_callback = progress
    return progress
