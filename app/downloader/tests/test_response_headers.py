from io import BytesIO
from unittest.mock import Mock, patch

import pytest
from curl_cffi import CurlECode, CurlError, CurlInfo
from curl_cffi.requests import AsyncSession
from curl_cffi.requests.exceptions import RequestException

from app.downloader.http_client import _ResponseHeaderSession


def parse_response(headers):
    values = {
        CurlInfo.EFFECTIVE_URL: b"https://example.org/",
        CurlInfo.RESPONSE_CODE: 200,
        CurlInfo.COOKIELIST: [],
        CurlInfo.PRIMARY_IP: b"127.0.0.1",
        CurlInfo.LOCAL_IP: b"127.0.0.1",
        CurlInfo.REDIRECT_URL: b"",
    }
    curl = Mock()
    curl.getinfo.side_effect = lambda option: values.get(option, 0)
    curl.get_reason_phrase.return_value = b"OK"
    return _ResponseHeaderSession()._parse_response(
        curl, BytesIO(b"article"), BytesIO(headers), "utf-8", True,
    )


@pytest.mark.parametrize("headers", [
    b"HTTP/2 200\r\nInvalid response header\r\n",
    b"HTTP/2 200\r\nX-Valid: yes\r\nTruncated\r\n",
    b"HTTP/2 200\r\n orphan continuation\r\n",
    b"HTTP/1.1 302 Found\r\nX-Valid: yes\r\n\r\nHTTP/2 200\r\nTruncated\r\n",
])
def test_malformed_response_headers_are_transport_errors(headers):
    with pytest.raises(RequestException) as caught:
        parse_response(headers)
    assert caught.value.code == CurlECode.WEIRD_SERVER_REPLY
    assert not isinstance(caught.value, ValueError)


def test_original_curl_failure_remains_chained():
    failure = CurlError("Invalid response header", CurlECode.BAD_FUNCTION_ARGUMENT)
    with pytest.raises(RequestException) as caught:
        try:
            raise failure
        except CurlError:
            parse_response(b"HTTP/2 200\r\nTruncated\r\n")
    assert caught.value.__context__ is failure


def test_valid_headers_keep_lone_cr_normalization_and_folded_values():
    response = parse_response(
        b"HTTP/2 200\r\nX-Value: first\rsecond\r\n\tfolded\r\n"
        b"Content-Type: text/html\r\n\r\n",
    )
    assert response.headers["x-value"] == "first second\tfolded"
    assert response.headers["content-type"] == "text/html"
    assert response.content == b"article"


def test_redirect_discards_headers_from_previous_response():
    response = parse_response(
        b"HTTP/1.1 302 Found\r\nTruncated\r\n\r\n"
        b"HTTP/2 200\r\nX-Final: yes\r\n\r\n",
    )
    assert dict(response.headers) == {"x-final": "yes"}


def test_unrelated_parser_value_errors_are_preserved():
    error = ValueError("application response validation failed")
    with patch.object(AsyncSession, "_parse_response", side_effect=error), pytest.raises(ValueError) as caught:
        parse_response(b"HTTP/2 200\r\nContent-Type: text/html\r\n\r\n")
    assert caught.value is error
