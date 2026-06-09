"""Web API 主入口 — FastAPI 管理面板后端。

提供统一的 REST API 和 WebSocket 端点，配置 CORS、静态文件服务、
全局异常处理和基础设施健康检查。

启动方式:
    uvicorn app.web.main:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

from datetime import timezone
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.config.settings import settings
from app.web.routes.dashboard import router as dashboard_router
from app.web.routes.etl import router as etl_router
from app.web.routes.monitor import router as monitor_router
from app.web.routes.tasks import router as tasks_router
from app.web.routes.templates import router as templates_router
from app.web.routes.ai_collect import router as ai_collect_router

logger = logging.getLogger(__name__)

# ── 静态文件路径 ─────────────────────────────────────────────────────────────
_STATIC_DIR = (Path(__file__).resolve().parent.parent.parent / "web-panel" / "dist").resolve()


# ── Lifespan ─────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(appy: FastAPI):
    """应用生命周期：启动时初始化、关闭时清理。"""
    logger.info("=" * 50)
    logger.info("Web API starting on port 8000")
    logger.info("  Templates dir: %s", settings.template_dir)
    logger.info("  Static files:  %s (exists=%s)", _STATIC_DIR, _STATIC_DIR.exists())
    logger.info("  MongoDB:       %s", "enabled" if settings.db_url else "disabled")
    logger.info("  Kafka:         %s", "enabled" if settings.kafka_brokers else "disabled")
    logger.info("=" * 50)
    yield
    logger.info("Web API shutting down")


# ── App Factory ──────────────────────────────────────────────────────────────


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用实例。"""

    appy = FastAPI(
        title="Patent Crawler API",
        description="分布式智能爬虫框架 — Web 管理面板后端",
        version="0.1.0",
        lifespan=lifespan,
    )

    # ── CORS 中间件 ──────────────────────────────────────────────────────
    appy.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "http://localhost:5173",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:5173",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── 全局异常处理 ─────────────────────────────────────────────────────
    @appy.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """捕获所有未处理异常，返回统一的 JSON 错误格式。"""
        logger.exception("Unhandled exception on %s %s", request.method, request.url.path)

        status_code = 500
        if hasattr(exc, "status_code"):
            status_code = getattr(exc, "status_code")

        return JSONResponse(
            status_code=status_code,
            content={
                "error": str(exc),
                "code": status_code,
            },
        )

    # ── 注册路由 ─────────────────────────────────────────────────────────
    appy.include_router(dashboard_router, prefix="/api")
    appy.include_router(etl_router, prefix="/api")
    appy.include_router(tasks_router, prefix="/api")
    appy.include_router(templates_router, prefix="/api")
    appy.include_router(monitor_router, prefix="/api")
    appy.include_router(ai_collect_router, prefix="/api")

    # ── 健康检查 ─────────────────────────────────────────────────────────
    @appy.get("/api/health")
    async def health_check() -> dict[str, Any]:
        """基础设施健康检查。

        返回各外部服务的真实连接状态（非 mock）。
        响应格式: { code: 0, data: { status: ..., services: ... }, ... }
        """
        from datetime import datetime as dt

        checks: dict[str, str] = {}

        # ── MongoDB ──
        if settings.db_url:
            try:
                from motor.motor_asyncio import AsyncIOMotorClient
                mongo_client = AsyncIOMotorClient(settings.db_url, serverSelectionTimeoutMS=3000)
                await mongo_client.admin.command("ping")
                checks["mongodb"] = "connected"
                mongo_client.close()
            except Exception:
                checks["mongodb"] = "unreachable"
        else:
            checks["mongodb"] = "disabled"

        # ── Redis ──
        if settings.redis_url:
            try:
                import redis.asyncio as aioredis
                r = aioredis.from_url(settings.redis_url, socket_connect_timeout=2)
                await r.ping()
                checks["redis"] = "connected"
                await r.close()
            except Exception:
                checks["redis"] = "unreachable"
        else:
            checks["redis"] = "disabled"

        # ── MinIO ──
        if settings.minio_endpoint:
            try:
                from minio import Minio
                minio_client = Minio(
                    settings.minio_endpoint,
                    access_key=settings.minio_access_key,
                    secret_key=settings.minio_secret_key,
                    secure=settings.minio_secure,
                )
                minio_client.list_buckets()
                checks["minio"] = "connected"
            except Exception:
                checks["minio"] = "unreachable"
        else:
            checks["minio"] = "disabled"

        # ── Kafka ──
        if settings.kafka_brokers:
            try:
                from aiokafka.admin import AIOKafkaAdminClient
                brokers = [b.strip() for b in settings.kafka_brokers.split(",") if b.strip()]
                admin = AIOKafkaAdminClient(bootstrap_servers=brokers)
                await admin.start()
                await admin.list_topics()
                checks["kafka"] = "connected"
                await admin.close()
            except Exception:
                checks["kafka"] = "unreachable"
        else:
            checks["kafka"] = "disabled"

        # ── Postgres ──
        if settings.pg_url and settings.pg_url != settings.__class__.model_fields["pg_url"].default:
            try:
                from app.storage.postgres_client import get_pg_client
                pg = get_pg_client()
                await pg.connect()
                checks["postgres"] = "connected" if pg._connected else "unreachable"
            except Exception:
                checks["postgres"] = "unreachable"
        else:
            checks["postgres"] = "disabled"

        # ── 文件系统 ──
        checks["templates_dir"] = "exists" if Path(settings.template_dir).is_dir() else "missing"
        checks["static_dir"] = "exists" if _STATIC_DIR.is_dir() else "missing"

        all_ok = all(v in ("connected", "disabled", "exists") for v in checks.values())

        return {
            "code": 0,
            "data": {
                "status": "healthy" if all_ok else "degraded",
                "services": checks,
            },
            "message": "success",
            "timestamp": dt.now(timezone.utc).isoformat(),
        }

    # ── 静态文件 + SPA fallback ─────────────────────────────────────────

    @appy.get("/", response_model=None)
    async def spa_root():
        """根路径：生产模式下返回 React SPA index.html。"""
        index_path = _STATIC_DIR / "index.html"
        if index_path.exists():
            return FileResponse(index_path)
        return {"message": "API is running. Use /docs for Swagger UI."}

    return appy


# ── 应用实例 ─────────────────────────────────────────────────────────────────

app = create_app()

