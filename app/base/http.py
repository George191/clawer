"""HTTP response and error contracts shared by request consumers."""

from dataclasses import dataclass


class DownloadError(Exception):
    def __init__(self, url: str, status_code: int | None = None, message: str = ""):
        self.url = url
        self.status_code = status_code
        super().__init__(f"Download failed: {url} (status={status_code}): {message}")


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
