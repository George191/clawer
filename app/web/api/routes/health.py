from datetime import datetime, timezone

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    """Return a lightweight liveness response without requiring a database."""
    return {
        "code": 0,
        "data": {
            "status": "healthy",
            "services": {"api": "available"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        "message": "ok",
    }
