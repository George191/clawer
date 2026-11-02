"""Publish the bundled Google source and create ordinary collection tasks."""

from pathlib import Path
from typing import Any

import yaml
from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator

from app.config.settings import settings
from app.models.template import PaginationType, SiteTemplate
from app.storage.postgres_client import get_pg_client
from app.web.api.models.tasks import WorkspaceTaskRequest
from app.web.services.ai_collect_store import ai_collect_store

SOURCE_DIR = Path(__file__).resolve().parent.parent / "collection_sources" / "google_search"
TEMPLATE_NAME = "google_search"
TEMPLATE_VERSION = "v1.1"


class GoogleSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    language: str = Field(default="zh-CN", pattern=r"^[a-z]{2,3}(?:-[A-Za-z]{2,4})?$")
    max_pages: int = Field(default=3, ge=1, le=100)
    fetch_details: bool = True

    @field_validator("query", mode="before")
    @classmethod
    def strip_query(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


async def create_google_search_task(payload: GoogleSearchRequest) -> dict[str, Any]:
    if not settings.db_url or not settings.db_name:
        raise HTTPException(status_code=503, detail="Google 搜索采集需要配置 MongoDB")
    if not settings.minio_endpoint or not settings.minio_bucket:
        raise HTTPException(status_code=503, detail="Google 搜索页面原文保存需要配置 MinIO")

    template = await ai_collect_store.get_template_definition(TEMPLATE_NAME, TEMPLATE_VERSION)
    if template is None:
        template = await ai_collect_store.release_template({
            "name": TEMPLATE_NAME,
            "version": TEMPLATE_VERSION,
            "title": "Google 搜索",
            "domain": "www.google.com",
            "status": "active",
            "yaml_content": (SOURCE_DIR / "template.yaml").read_text(encoding="utf-8"),
            "adapter_code": (SOURCE_DIR / "adapter.py").read_text(encoding="utf-8"),
            "description": "保存 Google 搜索结果、完整列表页面及详情页面原文。",
            "metadata": {"source": "google_search"},
        })

    parameters = {
        "query": payload.query,
        "language": payload.language,
        "max_pages": str(payload.max_pages),
        "fetch_details": str(payload.fetch_details).lower(),
    }
    try:
        definition = SiteTemplate(**yaml.safe_load(template["yaml_content"]))
        required_params = {"query", "language", "max_pages", "fetch_details"}
        if (
            template.get("status") != "active"
            or definition.name != TEMPLATE_NAME
            or definition.adapter != TEMPLATE_NAME
            or definition.data_type != "search_result"
            or definition.list_pagination is None
            or definition.list_pagination.type != PaginationType.PAGE_NUMBER
            or definition.list_pagination.start_page != 0
            or not required_params.issubset(param.name for param in definition.params)
        ):
            raise ValueError("Incompatible Google search template")
        definition.apply_params(parameters)
    except (TypeError, ValueError, yaml.YAMLError) as error:
        raise HTTPException(
            status_code=409, detail="Google 搜索模板未启用或参数不兼容，请检查模板库",
        ) from error

    # Reuse disabled collection routes: Mongo remains writable while downstream
    # workers and the immediate Kafka publisher exclude this Mongo-only source.
    pg = get_pg_client()
    route_params = {
        "template_name": TEMPLATE_NAME,
        "collection_name": "search_result_google_search",
        "data_type": "search_result",
    }
    await pg.execute(
        """
        INSERT INTO public.ai_collect_template_alias
            (template_name, collection_name, data_type, enabled)
        VALUES (:template_name, :collection_name, :data_type, false)
        ON CONFLICT DO NOTHING
        """,
        route_params,
    )
    route = await pg.fetch_one(
        """
        SELECT template_name, collection_name, data_type, enabled
        FROM public.ai_collect_template_alias
        WHERE collection_name = :collection_name
        """,
        {"collection_name": route_params["collection_name"]},
    )
    if (
        route is None
        or route["template_name"] != TEMPLATE_NAME
        or route["data_type"] != "search_result"
        or route["enabled"]
    ):
        raise HTTPException(status_code=409, detail="Google 搜索集合路由冲突，请检查集合归属及下游配置")

    task = WorkspaceTaskRequest(
        name=f"Google 搜索：{payload.query[:80]}",
        template_name=TEMPLATE_NAME,
        template_version=TEMPLATE_VERSION,
        parameters=parameters,
        schedule={"mode": "once"},
        policies={},
    )
    return await ai_collect_store.create_task(task.model_dump())
