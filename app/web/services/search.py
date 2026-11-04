"""Create ordinary tasks using the bundled, explicitly selected search engine."""

from pathlib import Path
from typing import Any, Literal

import yaml
from fastapi import HTTPException
from pydantic import Field

from app.config.settings import settings
from app.models.template import PaginationType, SiteTemplate
from app.storage.postgres_client import get_pg_client
from app.web.api.models.tasks import WorkspaceTaskRequest
from app.web.services.ai_collect_store import ai_collect_store
from app.web.services.google_search import GoogleSearchRequest

SOURCE_DIR = Path(__file__).resolve().parent.parent / "collection_sources"
ENGINES = {
    "duckduckgo": {"name": "duckduckgo_search", "version": "v1.0", "title": "DuckDuckGo 搜索", "domain": "html.duckduckgo.com"},
    "google": {"name": "google_search", "version": "v1.0", "title": "Google 搜索", "domain": "www.google.com"},
    "bing": {"name": "bing_search", "version": "v1.0", "title": "Bing 搜索", "domain": "www.bing.com"},
    "brave": {"name": "brave_search", "version": "v1.0", "title": "Brave 搜索", "domain": "search.brave.com"},
}


class SearchRequest(GoogleSearchRequest):
    engine: Literal["duckduckgo", "google", "bing", "brave"] = "brave"
    max_pages: int = Field(default=1, ge=1, le=100)


async def ensure_search_icon(template: dict[str, Any], engine: str) -> None:
    if template.get("icon"):
        return
    key = await ai_collect_store._upload_favicon(
        template["name"], template["version"], f"https://{ENGINES[engine]['domain']}/favicon.ico",
    )
    if not key:
        raise RuntimeError(f"Unable to upload search icon: {engine}")
    await get_pg_client().execute(
        """
        UPDATE public.ai_collect_templates SET icon = :icon
        WHERE name = :name AND version = :version AND COALESCE(icon, '') = ''
        """,
        {"name": template["name"], "version": template["version"], "icon": key},
    )


async def create_search_task(payload: SearchRequest) -> dict[str, Any]:
    if not settings.db_url or not settings.db_name:
        raise HTTPException(status_code=503, detail="搜索采集需要配置 MongoDB")
    if not settings.minio_endpoint or not settings.minio_bucket:
        raise HTTPException(status_code=503, detail="搜索页面原文保存需要配置 MinIO")
    proxy_url = payload.proxy_url.strip()
    if not proxy_url and not settings.proxy_pre_proxy_url:
        raise HTTPException(status_code=503, detail="搜索采集需要通过任务参数 proxy_url 指定代理或配置默认前置代理")

    engine = ENGINES[payload.engine]
    name, version = engine["name"], engine["version"]
    template = await ai_collect_store.get_template_definition(name, version)
    if template is None:
        source = SOURCE_DIR / name
        template = await ai_collect_store.release_template({
            **engine,
            "status": "active",
            "favicon_url": f"https://{engine['domain']}/favicon.ico",
            "yaml_content": (source / "template.yaml").read_text(encoding="utf-8"),
            "adapter_code": (source / "adapter.py").read_text(encoding="utf-8"),
            "description": f"保存 {engine['title']}结果、完整列表页面及详情页面原文。",
            "metadata": {"source": name, "engine": payload.engine},
        })

    parameters = {
        "query": payload.query,
        "language": payload.language,
        "max_pages": str(payload.max_pages),
        "fetch_details": str(payload.fetch_details).lower(),
    }
    if proxy_url:
        parameters["proxy_url"] = proxy_url
    try:
        definition = SiteTemplate(**yaml.safe_load(template["yaml_content"]))
        if proxy_url and "proxy_url" not in {param.name for param in definition.params}:
            raise HTTPException(status_code=409, detail="搜索模板不支持任务代理，请重新发布包含 proxy_url 的模板及适配器")
        if (
            template.get("status") != "active"
            or definition.name != name
            or definition.adapter != name
            or definition.data_type != "search_result"
            or definition.list_pagination is None
            or definition.list_pagination.type != PaginationType.PAGE_NUMBER
            or definition.list_pagination.start_page != 0
            or not {"query", "language", "max_pages", "fetch_details"}.issubset(param.name for param in definition.params)
        ):
            raise ValueError("Incompatible search template")
        definition.apply_params(parameters)
    except (TypeError, ValueError, yaml.YAMLError) as error:
        raise HTTPException(status_code=409, detail="搜索模板未启用或参数不兼容，请检查模板库") from error

    pg = get_pg_client()
    route_params = {"template_name": name, "collection_name": f"search_result_{name}", "data_type": "search_result"}
    routes = await pg.fetch_all(
        """
        SELECT template_name, collection_name, data_type, enabled
        FROM public.ai_collect_template_alias
        WHERE collection_name = :collection_name OR template_name = :template_name
        """,
        route_params,
    )
    if not routes:
        await pg.execute(
            """
            INSERT INTO public.ai_collect_template_alias
                (template_name, collection_name, data_type, enabled)
            VALUES (:template_name, :collection_name, :data_type, true)
            ON CONFLICT DO NOTHING
            """,
            route_params,
        )
        routes = await pg.fetch_all(
            """
            SELECT template_name, collection_name, data_type, enabled
            FROM public.ai_collect_template_alias
            WHERE collection_name = :collection_name OR template_name = :template_name
            """,
            route_params,
        )
    route = routes[0] if len(routes) == 1 else None
    if (
        route is None
        or route["template_name"] != name
        or route["collection_name"] != route_params["collection_name"]
        or route["data_type"] != "search_result"
        or not route["enabled"]
    ):
        raise HTTPException(status_code=409, detail="搜索集合路由未启用或归属冲突，请完成命名迁移及下游配置")
    await ensure_search_icon(template, payload.engine)
    task = WorkspaceTaskRequest(
        name=f"{engine['title']}：{payload.query[:80]}",
        template_name=name,
        template_version=version,
        parameters=parameters,
        schedule={"mode": "once"},
        policies={},
    )
    return await ai_collect_store.create_task(task.model_dump())
