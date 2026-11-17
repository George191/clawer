"""Standalone numeric-loopback synthetic Push ingress; never mount this on the ordinary app."""

import asyncio
from collections.abc import Callable
from hashlib import sha256
import json
import re
import time
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send
import uvicorn

from app.web.services.push_inbox import accept_push, configured, local_inbox_database, producer_key, strict_base64

HOST, PORT = "127.0.0.1", 8765
PATH = "/synthetic-push/v1/inbox"
BODY_LIMIT = 4096
TIMESTAMP_TOLERANCE = 300
_HEADERS = {
    b"x-inbox-request-id", b"x-inbox-timestamp", b"x-inbox-signature", b"content-type",
    b"content-length", b"transfer-encoding",
}
_DENIED = {b"forwarded", b"x-real-ip", b"via", b"content-encoding"}


def _unauthorized() -> HTTPException:
    return HTTPException(401, "Unauthorized synthetic producer")


def signing_message(timestamp: str, identity: UUID, digest: str) -> bytes:
    return f"synthetic-push-v1\nPOST\n{PATH}\n{timestamp}\n{identity}\n{digest}\n".encode("ascii")


def _headers(scope: Scope) -> tuple[UUID, str, bytes, int | None]:
    peer = scope.get("client")
    if not isinstance(peer, (tuple, list)) or len(peer) != 2 or peer[0] != HOST:
        raise _unauthorized()
    entries = scope.get("headers", ())
    if len(entries) > 40 or sum(len(name) + len(value) for name, value in entries) > 8192:
        raise _unauthorized()
    recognized = {}
    for raw_name, value in entries:
        name = raw_name.lower()
        if name in _DENIED or name.startswith(b"x-forwarded-"):
            raise _unauthorized()
        if name in _HEADERS:
            if name in recognized:
                raise _unauthorized()
            recognized[name] = value
    try:
        raw_id = recognized[b"x-inbox-request-id"].decode("ascii")
        if len(raw_id) != 36:
            raise ValueError
        identity = UUID(raw_id)
        raw_stamp = recognized[b"x-inbox-timestamp"].decode("ascii")
        signature = strict_base64(recognized[b"x-inbox-signature"], 64)
        if (str(identity) != raw_id or identity.version != 4
            or not re.fullmatch(r"[1-9][0-9]{0,10}", raw_stamp) or signature is None
            or recognized.get(b"content-type") != b"application/json"):
            raise ValueError
        length, encoding = recognized.get(b"content-length"), recognized.get(b"transfer-encoding")
        if encoding is not None and (encoding.lower() != b"chunked" or length is not None):
            raise ValueError
        if length is not None and not re.fullmatch(rb"(?:0|[1-9][0-9]{0,9})", length):
            raise ValueError
        return identity, raw_stamp, signature, int(length) if length is not None else None
    except (KeyError, ValueError, UnicodeError):
        raise _unauthorized() from None


def _date_allowed(timestamp: str) -> bool:
    return abs(int(time.time()) - int(timestamp)) <= TIMESTAMP_TOLERANCE


async def _read_body(request: Request, length: int | None) -> bytes:
    if length is not None and length > BODY_LIMIT:
        raise HTTPException(413, "Synthetic request body is too large")
    body = bytearray()
    try:
        async with asyncio.timeout(3):
            async for chunk in request.stream():
                if len(chunk) > BODY_LIMIT - len(body):
                    raise HTTPException(413, "Synthetic request body is too large")
                body.extend(chunk)
    except (ClientDisconnect, TimeoutError):
        raise HTTPException(400, "Incomplete synthetic request body") from None
    if length is not None and len(body) != length:
        raise HTTPException(400, "Incomplete synthetic request body")
    return bytes(body)


def _unique(pairs: list[tuple]) -> dict:
    output = {}
    for name, value in pairs:
        if name in output:
            raise ValueError("duplicate JSON key")
        output[name] = value
    return output


def _payload(body: bytes) -> tuple[int, str] | None:
    try:
        value = json.loads(body.decode("utf-8"), object_pairs_hook=_unique)
        if not isinstance(value, dict) or set(value) != {"schemaVersion", "entityId", "value"}:
            return None
        version, key, fixture = value["schemaVersion"], value["entityId"], value["value"]
        if (type(version) is not int or version != 1 or not isinstance(key, str)
            or not re.fullmatch(r"[1-9][0-9]{0,18}", key) or int(key) >= 2**63
            or not isinstance(fixture, str) or not re.fullmatch(r"fixture-[a-z0-9-]{1,48}", fixture)):
            return None
        return int(key), fixture
    except (ValueError, TypeError, RecursionError, UnicodeError):
        return None


def create_ingress(sessions: Callable):
    """The ASGI app has no framework mount, docs, redirects, or proxy trust."""
    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                else:
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["type"] != "http":
            await send({"type": "websocket.close", "code": 1008})
            return
        try:
            if (scope.get("path") != PATH or scope.get("raw_path", b"") != PATH.encode("ascii")
                or scope.get("root_path") or scope.get("query_string")):
                raise HTTPException(404, "Not found")
            if scope.get("method") != "POST":
                raise HTTPException(405, "Method not allowed")
            if not configured():
                raise HTTPException(404, "Not found")
            identity, stamp, signature, length = _headers(scope)
            if not _date_allowed(stamp):
                raise _unauthorized()
            body = await _read_body(Request(scope, receive), length)
            digest = sha256(body).hexdigest()
            key = producer_key()
            if key is None or not _date_allowed(stamp):
                raise _unauthorized()
            try:
                key.verify(signature, signing_message(stamp, identity, digest))
            except (InvalidSignature, ValueError):
                raise _unauthorized() from None
            payload = _payload(body)
            async with asyncio.timeout(4):
                async with sessions() as session:
                    result = await accept_push(session, identity, digest, payload)
            reply = JSONResponse(result.model_dump(mode="json"), status_code=201)
        except HTTPException as error:
            reply = JSONResponse({"detail": error.detail}, status_code=error.status_code)
        except Exception:
            # Never log driver diagnostics, authentication material or request payload.
            reply = JSONResponse({"detail": "Synthetic inbox unavailable; retry the same request ID"}, status_code=503)
        reply.headers["Cache-Control"] = "no-store"
        await reply(scope, receive, send)

    return app


async def _serve() -> None:
    url = local_inbox_database()
    if not configured() or url is None:
        raise SystemExit("Synthetic Push ingress requires explicit local provisioning")
    engine = create_async_engine(
        url, pool_size=4, max_overflow=0, pool_timeout=1, hide_parameters=True, connect_args={"timeout": 2},
    )
    try:
        app = create_ingress(async_sessionmaker(engine, expire_on_commit=False))
        server = uvicorn.Server(uvicorn.Config(
            app, host=HOST, port=PORT, proxy_headers=False, forwarded_allow_ips="", ws="none", http="h11",
            access_log=False, timeout_keep_alive=3, timeout_graceful_shutdown=5, limit_concurrency=32,
            server_header=False, lifespan="on", loop="asyncio", h11_max_incomplete_event_size=8192,
        ))
        await server.serve()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_serve())
