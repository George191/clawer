"""HTTP response and error contracts shared by request consumers."""

from dataclasses import dataclass
import errno
from pathlib import Path


class DownloadError(Exception):
    def __init__(self, url: str, status_code: int | None = None, message: str = ""):
        self.url = url
        self.status_code = status_code
        super().__init__(f"Download failed: {url} (status={status_code}): {message}")


class DownloadCleanupError(DownloadError):
    """Transport cleanup is unconfirmed; this run must stop without retrying."""


def is_download_file_error(error: BaseException) -> bool:
    return isinstance(error, OSError) and error.errno in {
        errno.ENOSPC, errno.EDQUOT, errno.EACCES, errno.EROFS, errno.EIO,
    }


class ResponseValidationError(ValueError):
    """A fetched response could not be parsed or failed source validation."""

    proxy_failure_marked = False


@dataclass(frozen=True, slots=True)
class DownloadResponse:
    source_url: str
    final_url: str
    status_code: int
    headers: dict[str, str]
    data: bytes

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";", 1)[0].strip().lower()

    def read_prefix(self, limit: int = 32768) -> bytes:
        if limit < 0:
            raise ValueError("Prefix length must be non-negative")
        return self.data[:limit]


@dataclass(frozen=True, slots=True)
class FileDownloadResponse:
    source_url: str
    final_url: str
    status_code: int
    headers: dict[str, str]
    path: Path
    size: int

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";", 1)[0].strip().lower()

    def read_prefix(self, limit: int = 32768) -> bytes:
        if limit < 0:
            raise ValueError("Prefix length must be non-negative")
        with self.path.open("rb") as source:
            return source.read(limit)
