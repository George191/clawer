from typing import Any

from fastapi import APIRouter

router = APIRouter(tags=["infrastructure"])


@router.get("/kafka/topics")
async def kafka_topics() -> list[dict[str, Any]]:
    return []


@router.get("/redis/offsets")
async def redis_offsets() -> list[dict[str, Any]]:
    return []


@router.get("/scheduler/queue")
async def scheduler_queue() -> list[dict[str, Any]]:
    return []


@router.post("/scheduler/enqueue")
async def enqueue(payload: dict[str, Any]) -> dict[str, Any]:
    return {"accepted": False, "message": "队列服务未配置"}
