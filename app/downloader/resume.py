"""Validate byte ranges before appending a retried download body."""

from email.utils import parsedate_to_datetime
import re


class ResumeProtocolError(ValueError):
    """A partial response cannot safely be joined to bytes already received."""


class DownloadTruncatedError(ConnectionError):
    """The transfer ended before its declared response or object length."""


class ResumeState:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.offset = 0
        self.total = 0
        self.headers: dict[str, str] = {}
        self.final_url = ""
        self._validator: tuple[str, str] | None = None
        self._response_end: int | None = None

    def request_headers(self) -> dict[str, str]:
        headers = {"Accept-Encoding": "identity"}
        if self.offset and self._validator:
            headers.update({"Range": f"bytes={self.offset}-", "If-Range": self._validator[1]})
        return headers

    @staticmethod
    def _length(headers: dict[str, str]) -> int | None:
        raw = headers.get("content-length")
        if raw is None:
            return None
        if not raw.isascii() or not raw.isdecimal():
            raise ResumeProtocolError("Invalid download Content-Length")
        return int(raw)

    @staticmethod
    def _identity(headers: dict[str, str]) -> bool:
        return headers.get("content-encoding", "identity").strip().lower() in ("", "identity")

    @staticmethod
    def _read_validator(headers: dict[str, str]) -> tuple[str, str] | None:
        etag = headers.get("etag", "").strip()
        if etag:
            return ("etag", etag) if re.fullmatch(r'"[\x21\x23-\x7e\x80-\xff]*"', etag) else None
        modified = headers.get("last-modified", "").strip()
        if modified:
            try:
                modified_at = parsedate_to_datetime(modified)
                response_at = parsedate_to_datetime(headers.get("date", ""))
                if (modified_at.tzinfo is not None and response_at.tzinfo is not None
                        and (response_at - modified_at).total_seconds() >= 60):
                    return "last-modified", modified
            except (ValueError, TypeError, OverflowError):
                pass
        return None

    def start_response(self, status: int, headers, final_url: str) -> bool:
        """Return True when the caller must discard its previous body bytes."""
        headers = {str(key).lower(): str(value).strip() for key, value in headers.items()}
        if status == 200:
            self.reset()
            self.headers = headers
            self.final_url = final_url
            if self._identity(headers):
                length = self._length(headers)
                self.total = length or 0
                self._response_end = length
                self._validator = self._read_validator(headers)
            return True
        if status != 206 or not self.offset or not self._validator:
            raise ResumeProtocolError("Unexpected download partial response")
        if final_url != self.final_url or not self._identity(headers):
            raise ResumeProtocolError("Download representation changed during resume")
        name, validator = self._validator
        if headers.get(name) != validator:
            raise ResumeProtocolError("Download validator changed during resume")
        match = re.fullmatch(r"bytes ([0-9]+)-([0-9]+)/([0-9]+)", headers.get("content-range", ""))
        if match is None:
            raise ResumeProtocolError("Invalid download Content-Range")
        start, end, total = map(int, match.groups())
        if start != self.offset or end < start or end >= total or self.total and total != self.total:
            raise ResumeProtocolError("Download Content-Range does not match saved bytes")
        length = self._length(headers)
        if length is not None and length != end - start + 1:
            raise ResumeProtocolError("Download range length does not match Content-Range")
        self.total = total
        self._response_end = end + 1
        return False

    def advance(self, size: int) -> None:
        self.offset += size
        if self._response_end is not None and self.offset > self._response_end:
            raise ResumeProtocolError("Download body exceeds its declared length")

    def finish(self) -> None:
        if self._response_end is not None and self.offset != self._response_end:
            raise DownloadTruncatedError("Download incomplete: response ended before its declared length")
        if self.total and self.offset != self.total:
            raise DownloadTruncatedError("Download incomplete: body ended before the complete object length")

    def complete_headers(self) -> dict[str, str]:
        headers = dict(self.headers)
        headers.pop("content-range", None)
        headers.pop("transfer-encoding", None)
        if self._identity(headers):
            headers["content-length"] = str(self.offset)
        return headers
