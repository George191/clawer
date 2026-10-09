from fastapi import APIRouter

router = APIRouter(prefix="/monitor", tags=["monitor"])


@router.get("/stats")
async def stats() -> dict[str, float | int]:
    return {
        "reqRate": 0,
        "successRate": 100,
        "antiCrawlTriggers": 0,
        "proxyAvailable": 0,
        "proxyTotal": 0,
    }
