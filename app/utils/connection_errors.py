"""Identify transient connection failures without retrying application errors."""

import errno
import socket

from curl_cffi.const import CurlECode
from curl_cffi.requests import exceptions as curl_errors
from pymongo.errors import ConnectionFailure, PyMongoError
from redis.exceptions import AuthenticationError, AuthorizationError
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as SQLTimeoutError
from urllib3 import exceptions as urllib_errors

from app.base.http import DownloadCleanupError

try:
    from playwright.async_api import TimeoutError as BrowserTimeoutError
except ImportError:
    _BROWSER_TIMEOUT_ERRORS = ()
else:
    _BROWSER_TIMEOUT_ERRORS = (BrowserTimeoutError,)

_NETWORK_ERRNOS = {
    errno.ECONNABORTED, errno.ECONNREFUSED, errno.ECONNRESET,
    errno.ENETDOWN, errno.ENETUNREACH, errno.EHOSTUNREACH,
    errno.ETIMEDOUT, errno.EPIPE,
}

_NON_RETRYABLE_HTTP_ERRORS = (
    curl_errors.CookieConflict, curl_errors.SessionClosed, curl_errors.ImpersonateError,
    curl_errors.InvalidJSONError, curl_errors.URLRequired, curl_errors.MissingSchema,
    curl_errors.InvalidSchema, curl_errors.InvalidURL, curl_errors.InvalidHeader,
    curl_errors.TooManyRedirects, curl_errors.ContentDecodingError,
    curl_errors.StreamConsumedError, curl_errors.UnrewindableBodyError,
    urllib_errors.LocationValueError, urllib_errors.InvalidHeader,
    urllib_errors.ResponseError, urllib_errors.DecodeError, urllib_errors.HeaderParsingError,
    urllib_errors.HostChangedError, urllib_errors.TimeoutStateError,
    urllib_errors.ClosedPoolError, urllib_errors.FullPoolError,
    urllib_errors.ResponseNotChunked, urllib_errors.BodyNotHttplibCompatible,
    urllib_errors.UnrewindableBodyError,
)
_NON_RETRYABLE_CURL_CODES = {
    CurlECode.UNSUPPORTED_PROTOCOL, CurlECode.FAILED_INIT, CurlECode.URL_MALFORMAT,
    CurlECode.NOT_BUILT_IN, CurlECode.UNKNOWN_OPTION, CurlECode.SETOPT_OPTION_SYNTAX,
    CurlECode.OUT_OF_MEMORY,
}
_CURL_HTTP_TRANSPORT_CODES = {
    CurlECode.HTTP2, CurlECode.HTTP2_STREAM, CurlECode.HTTP3, CurlECode.PARTIAL_FILE,
}


def is_retryable_connection_error(error: BaseException) -> bool:
    """Inspect wrapped causes too (some adapters redact transport exceptions)."""
    seen: set[int] = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, DownloadCleanupError):
            return False
        if isinstance(error, (AuthenticationError, AuthorizationError, *_NON_RETRYABLE_HTTP_ERRORS)):
            return False
        if isinstance(error, curl_errors.RequestException):
            code = getattr(error, "code", 0)
            if code in _NON_RETRYABLE_CURL_CODES:
                return False
            if isinstance(error, curl_errors.HTTPError):
                status = getattr(getattr(error, "response", None), "status_code", None)
                if status is not None and status >= 400:
                    return False
                return isinstance(error, curl_errors.IncompleteRead) or code in _CURL_HTTP_TRANSPORT_CODES
            return True
        if isinstance(error, urllib_errors.MaxRetryError):
            error = error.reason
            continue
        if isinstance(error, _BROWSER_TIMEOUT_ERRORS):
            return True
        if isinstance(error, ConnectionFailure | ConnectionError | TimeoutError
                      | socket.gaierror | RedisConnectionError | RedisTimeoutError
                      | SQLTimeoutError | urllib_errors.HTTPError):
            return True
        if isinstance(error, PyMongoError) and error.timeout:
            return True
        sqlstate = str(getattr(error, "sqlstate", None) or getattr(error, "pgcode", "") or "")
        if sqlstate.startswith("08") or sqlstate in {"57P01", "57P02", "57P03", "53300"}:
            return True
        if isinstance(error, DBAPIError) and error.connection_invalidated:
            return True
        if isinstance(error, OSError) and error.errno in _NETWORK_ERRNOS:
            return True
        error = (
            error.orig if isinstance(error, DBAPIError)
            else error.__cause__ or error.__context__
        )
    return False
