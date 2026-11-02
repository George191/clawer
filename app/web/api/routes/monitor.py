from fastapi import APIRouter, Depends

from app.web.api.dependencies.common import get_current_user

router = APIRouter(prefix="/monitor", tags=["monitor"], dependencies=[Depends(get_current_user)])


@router.get("/stats")
async def stats() -> dict[str, float | int | str | bool | None]:
    return {
        "reqRate": None,
        "successRate": None,
        "antiCrawlTriggers": None,
        "proxyAvailable": None,
        "proxyTotal": None,
        "available": False,
        "reason": "尚未接入请求、反爬和代理池的运行指标；未知值不代表零流量或全部成功",
    }
