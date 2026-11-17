"""Domain-separated, actor-bound continuation for the fixed synthetic range preview."""
from base64 import b64decode, urlsafe_b64encode
from dataclasses import dataclass
from hashlib import sha256
import hmac
import json
import re
from time import time
from uuid import UUID

from fastapi import HTTPException

from app.web.api.models.index_preview_sandbox import IndexSandboxQuery
from app.web.core.config import settings

MAX_PAGES = 10
CURSOR_LIFETIME = 300
_TAG = re.compile(r"[A-Za-z0-9_-]+")
_INT8 = re.compile(r"(?:0|-?[1-9][0-9]{0,18})")
_DIGEST = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class IndexCursor:
    page: int
    expires: int
    last_id: str
    scope: str


def cursor_key() -> bytes | None:
    secret = settings.SECRET_KEY.encode("utf-8")
    if "SECRET_KEY" not in settings.model_fields_set or len(secret) < 32 or not secret.strip():
        return None
    return hmac.digest(secret, b"ontology:index-preview-sandbox:keyset:v1", "sha256")


def epoch_seconds() -> int:
    return int(time())


def invalid_cursor() -> HTTPException:
    return HTTPException(409, {"code": "cursor_invalid", "message": "This continuation is invalid or expired. Restart the range preview."})


def physical_scope(key: bytes, identity: tuple[int, int, int, int]) -> str:
    return hmac.new(key, b"catalog:v1\x00" + _encode(identity), sha256).hexdigest()


def _encode(fields) -> bytes:
    return json.dumps(fields, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _filters(query: IndexSandboxQuery) -> str:
    return sha256(_encode([query.mode, query.idFrom, query.idTo, query.limit])).hexdigest()


def _base64(raw: bytes) -> str:
    return urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode(part: str) -> bytes:
    if not _TAG.fullmatch(part):
        raise ValueError
    raw = b64decode(part + "=" * (-len(part) % 4), altchars=b"-_", validate=True)
    if _base64(raw) != part:
        raise ValueError
    return raw


def _mac(key: bytes, actor: UUID, payload: bytes) -> bytes:
    return hmac.digest(key, b"continuation:v1\x00" + actor.bytes + payload, "sha256")


def next_cursor(key: bytes, actor: UUID, query: IndexSandboxQuery, state: IndexCursor) -> str:
    payload = _encode([state.page, state.expires, state.last_id, _filters(query), state.scope])
    return _base64(payload) + "." + _base64(_mac(key, actor, payload))


def read_cursor(key: bytes | None, actor: UUID, query: IndexSandboxQuery) -> IndexCursor:
    try:
        if key is None or query.cursor is None:
            raise ValueError
        encoded, signature = query.cursor.split(".")
        payload = _decode(encoded)
        if not hmac.compare_digest(_decode(signature), _mac(key, actor, payload)):
            raise ValueError
        page, expires, anchor, filters, scope = json.loads(payload)
        now = epoch_seconds()
        if (type(page) is not int or not 2 <= page <= MAX_PAGES
            or type(expires) is not int or not now < expires <= now + CURSOR_LIFETIME
            or filters != _filters(query) or not isinstance(scope, str) or not _DIGEST.fullmatch(scope)
            or not isinstance(anchor, str) or not _INT8.fullmatch(anchor)
            or not -(2**63) <= int(anchor) < 2**63
            or (query.idFrom is not None and int(anchor) < int(query.idFrom))
            or (query.idTo is not None and int(anchor) >= int(query.idTo))):
            raise ValueError
        return IndexCursor(page, expires, anchor, scope)
    except (ValueError, TypeError, UnicodeError):
        raise invalid_cursor() from None
