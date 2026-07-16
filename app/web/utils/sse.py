"""SSE (Server-Sent Events) utilities."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

logger = logging.getLogger(__name__)


def sse_event(name: str, data: dict[str, Any]) -> str:
    """Generate a properly formatted SSE event string."""
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def sse_wrapper(
    generator: AsyncGenerator[str, None],
    request,
    context: str = "sse_stream",
) -> AsyncGenerator[str, None]:
    """Wrapper for SSE streams with connection monitoring."""
    try:
        async for chunk in generator:
            if await request.is_disconnected():
                logger.info(f"{context} connection disconnected")
                break
            yield chunk
    except Exception as exc:
        logger.exception(f"{context} error")
        yield sse_event("error", {"code": "STREAM_ERROR", "message": str(exc)})