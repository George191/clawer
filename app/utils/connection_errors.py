"""Identify transient connection failures without retrying application errors."""

import errno
import socket

from curl_cffi.requests.exceptions import HTTPError, RequestException
from pymongo.errors import ConnectionFailure
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as SQLTimeoutError
from urllib3.exceptions import HTTPError as UrllibHTTPError

_NETWORK_ERRNOS = {
    errno.ECONNABORTED, errno.ECONNREFUSED, errno.ECONNRESET,
    errno.ENETDOWN, errno.ENETUNREACH, errno.EHOSTUNREACH,
    errno.ETIMEDOUT, errno.EPIPE,
}


def is_retryable_connection_error(error: BaseException) -> bool:
    """Inspect wrapped causes too (some adapters redact transport exceptions)."""
    seen: set[int] = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, HTTPError):
            return False
        if isinstance(error, ConnectionFailure | ConnectionError | TimeoutError
                      | socket.gaierror | RedisConnectionError | RedisTimeoutError
                      | SQLTimeoutError | UrllibHTTPError | RequestException):
            return True
        if isinstance(error, DBAPIError) and error.connection_invalidated:
            return True
        if isinstance(error, OSError) and error.errno in _NETWORK_ERRNOS:
            return True
        error = error.__cause__ or error.__context__
    return False
