from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from app.config.settings import settings
from app.web.routes.tasks import _tasks_store

_AI_COLLECT_SCOPE_PATH = Path(__file__).resolve().parent.parent / "policies" / "ai_collect_scope.json"

_DATA_TYPE_LABELS = {
    "patent": "专利情报",
    "news": "新闻资讯",
    "navwarn": "航警通告",
    "intelligence": "情报资料",
    "other": "其他数据",
}

_SERVICE_META = [
    {
        "key": "crawler",
        "title": "Crawler",
        "accent": "#8AB4FF",
        "description": "模板驱动采集入口，负责参数渲染、页面抓取、解析与反爬策略编排。",
        "command": "python -m app.crawler.main",
        "dependencies": ("templates", "http"),
        "secondary": lambda: f"模板并发 {settings.max_concurrent_tasks} / 页并发 {settings.page_concurrency}",
    },
    {
        "key": "downloader",
        "title": "Downloader",
        "accent": "#65D5A3",
        "description": "监听原始记录，拉取 PDF、图片等资源并落到 MinIO 或本地文件系统。",
        "command": "python -m app.downloader.main",
        "dependencies": ("mongodb", "minio"),
        "secondary": lambda: "默认轮询 10s / 批量 50",
    },
    {
        "key": "syncer",
        "title": "Syncer",
        "accent": "#F6C35B",
        "description": "从 MongoDB 扫描已处理记录，推送 Kafka 供 ETL 分层消费。",
        "command": "python -m app.syncer.main",
        "dependencies": ("mongodb", "kafka"),
        "secondary": lambda: "默认轮询 10s / 批量 50",
    },
    {
        "key": "etl",
        "title": "ETL",
        "accent": "#FF7A7A",
        "description": "六层 Worker 将消息落入 RDS、ODS、TASK、DWD、DWS、DIM，并向 ADS 输出。",
        "command": "python -m app.etl.main --layer all",
        "dependencies": ("kafka", "postgres"),
        "secondary": lambda: "RDS / ODS / TASK / DWD / DWS / DIM",
    },
]

_ETL_LAYER_META = [
    {
        "key": "rds",
        "label": "RDS 原始层",
        "schema": "ts_rds",
        "topic_in": lambda: settings.etl_raw_topic,
        "topic_out": lambda: settings.etl_rds_topic,
        "focus": "接收采集原始消息，保留最小清洗的事实记录。",
    },
    {
        "key": "ods",
        "label": "ODS 标准层",
        "schema": "ts_ods",
        "topic_in": lambda: settings.etl_rds_topic,
        "topic_out": lambda: settings.etl_ods_topic,
        "focus": "按数据主题标准化字段，形成稳定的业务对象。",
    },
    {
        "key": "task",
        "label": "TASK 任务层",
        "schema": "ts_task",
        "topic_in": lambda: settings.etl_task_topic,
        "topic_out": lambda: settings.etl_ads_topic,
        "focus": "处理 PDF 转 Markdown 等异步富化任务。",
    },
    {
        "key": "dwd",
        "label": "DWD 明细层",
        "schema": "ts_dwd",
        "topic_in": lambda: settings.etl_ods_topic,
        "topic_out": lambda: settings.etl_dwd_topic,
        "focus": "沉淀可复用明细事实，为聚合和算法消费做准备。",
    },
    {
        "key": "dws",
        "label": "DWS 汇总层",
        "schema": "ts_dws",
        "topic_in": lambda: settings.etl_dwd_topic,
        "topic_out": lambda: settings.etl_dws_topic,
        "focus": "按主题域聚合指标，服务查询与看板。",
    },
    {
        "key": "dim",
        "label": "DIM 维度层",
        "schema": "ts_dim",
        "topic_in": lambda: settings.etl_ods_topic,
        "topic_out": lambda: settings.etl_dim_topic,
        "focus": "维护企业、区域、分类等共享维度资产。",
    },
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _status_rank(status: str) -> int:
    return {"healthy": 3, "degraded": 2, "inactive": 1}.get(status, 0)


def _dependency_configured(name: str, template_count: int) -> bool:
    if name == "templates":
        return template_count > 0
    if name == "http":
        return bool(settings.http_user_agent)
    if name == "mongodb":
        return bool(settings.db_url)
    if name == "minio":
        return bool(settings.minio_endpoint)
    if name == "kafka":
        return bool(settings.kafka_brokers)
    if name == "postgres":
        return bool(settings.pg_url)
    return False


def _status_for_dependencies(dependencies: tuple[str, ...], template_count: int) -> str:
    ready_count = sum(1 for dependency in dependencies if _dependency_configured(dependency, template_count))
    if ready_count == len(dependencies):
        return "healthy"
    if ready_count == 0:
        return "inactive"
    return "degraded"


def _load_scope_limits() -> dict[str, int]:
    defaults = {
        "max_template_pages": 100,
        "max_dry_run_limit": 100,
        "max_generated_adapter_lines": 500,
        "blocked_exact_hosts": 4,
    }
    if not _AI_COLLECT_SCOPE_PATH.exists():
        return defaults

    try:
        payload = json.loads(_AI_COLLECT_SCOPE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return defaults

    limits = payload.get("limits", {})
    url_rules = payload.get("url_rules", {})
    return {
        "max_template_pages": _safe_int(limits.get("max_template_pages"), defaults["max_template_pages"]),
        "max_dry_run_limit": _safe_int(limits.get("max_dry_run_limit"), defaults["max_dry_run_limit"]),
        "max_generated_adapter_lines": _safe_int(
            limits.get("max_generated_adapter_lines"),
            defaults["max_generated_adapter_lines"],
        ),
        "blocked_exact_hosts": len(url_rules.get("blocked_exact_hosts", [])) or defaults["blocked_exact_hosts"],
    }


def _load_templates() -> list[dict[str, Any]]:
    template_dir = Path(settings.template_dir)
    if not template_dir.exists():
        return []

    templates: list[dict[str, Any]] = []
    for ext in ("*.yaml", "*.yml"):
        for file_path in sorted(template_dir.glob(ext)):
            try:
                payload = yaml.safe_load(file_path.read_text(encoding="utf-8"))
            except Exception:
                continue

            if not isinstance(payload, dict):
                continue

            base_url = str(payload.get("base_url", "")).strip()
            domain = urlparse(base_url).hostname or ""
            fields = 0
            for section in ("list_fields", "detail_fields"):
                section_fields = payload.get(section, [])
                if isinstance(section_fields, list):
                    fields += sum(1 for item in section_fields if isinstance(item, dict))

            templates.append(
                {
                    "name": payload.get("name", file_path.stem),
                    "displayName": payload.get("display_name") or payload.get("name", file_path.stem),
                    "dataType": payload.get("data_type", "other"),
                    "domain": domain,
                    "description": str(payload.get("description", "")).strip(),
                    "fieldCount": fields,
                    "updatedAt": datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc).isoformat(),
                }
            )
    return templates


def _build_source_groups(templates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for template in templates:
        grouped[str(template.get("dataType", "other"))].append(template)

    source_groups: list[dict[str, Any]] = []
    for data_type, items in grouped.items():
        domains = sorted({item["domain"] for item in items if item.get("domain")})
        source_groups.append(
            {
                "key": data_type,
                "label": _DATA_TYPE_LABELS.get(data_type, data_type.title()),
                "count": len(items),
                "fieldCount": sum(_safe_int(item.get("fieldCount")) for item in items),
                "domains": domains[:4],
                "templates": [item["name"] for item in items[:4]],
                "updatedAt": max(item["updatedAt"] for item in items),
            }
        )

    return sorted(source_groups, key=lambda item: (-item["count"], item["label"]))


def _build_stage_cards(templates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    template_count = len(templates)
    unique_domains = len({template["domain"] for template in templates if template.get("domain")})
    queued_or_running = sum(
        1
        for task in _tasks_store.values()
        if str(task.get("status", "")) in {"queued", "running", "paused"}
    )
    data_types = len({template["dataType"] for template in templates})

    primary_metrics = {
        "crawler": f"{template_count} 个模板 / {unique_domains} 个源站",
        "downloader": "Mongo -> MinIO 资源沉淀",
        "syncer": "Mongo -> Kafka 增量推送",
        "etl": f"{len(_ETL_LAYER_META)} 层 Worker / {data_types or 1} 个主题域",
    }
    badges = {
        "crawler": f"{queued_or_running} 个任务待编排",
        "downloader": "附件、PDF、图片落盘",
        "syncer": "为 ETL 提供增量消息",
        "etl": "标准层与指标层联动",
    }

    stage_cards: list[dict[str, Any]] = []
    for meta in _SERVICE_META:
        status = _status_for_dependencies(meta["dependencies"], template_count)
        stage_cards.append(
            {
                "key": meta["key"],
                "title": meta["title"],
                "accent": meta["accent"],
                "status": status,
                "description": meta["description"],
                "command": meta["command"],
                "primaryMetric": primary_metrics[meta["key"]],
                "secondaryMetric": meta["secondary"](),
                "badge": badges[meta["key"]],
                "dependencies": list(meta["dependencies"]),
            }
        )

    return stage_cards


def _build_task_board(templates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for task_id, payload in _tasks_store.items():
        status = str(payload.get("status", "queued"))
        tasks.append(
            {
                "id": task_id,
                "name": str(payload.get("template", task_id)),
                "template": str(payload.get("template", "")),
                "status": status,
                "progress": _safe_int(payload.get("progress"), 0),
                "records": _safe_int(payload.get("records"), 0),
                "startedAt": payload.get("started_at"),
                "kind": "live",
                "stage": "crawler" if status in {"queued", "running", "paused"} else "etl",
            }
        )

    tasks.sort(key=lambda item: item.get("startedAt") or "", reverse=True)
    if tasks:
        return tasks[:6]

    suggested: list[dict[str, Any]] = []
    for index, template in enumerate(templates[:6]):
        data_type = str(template.get("dataType", "other"))
        default_mode = {
            "navwarn": "增量巡检",
            "news": "半小时轮询",
            "patent": "批量抓取",
            "intelligence": "文档富化",
        }.get(data_type, "模板待发布")
        suggested.append(
            {
                "id": f"suggested-{index}",
                "name": str(template["displayName"]),
                "template": str(template["name"]),
                "status": "planned",
                "progress": 0,
                "records": 0,
                "startedAt": template.get("updatedAt"),
                "kind": "suggested",
                "stage": "crawler",
                "mode": default_mode,
            }
        )
    return suggested


def _build_etl_layers() -> list[dict[str, Any]]:
    kafka_ready = bool(settings.kafka_brokers)
    postgres_ready = bool(settings.pg_url)
    layers: list[dict[str, Any]] = []

    for meta in _ETL_LAYER_META:
        topic_in = meta["topic_in"]()
        topic_out = meta["topic_out"]()
        configured = bool(topic_in) and bool(topic_out) and kafka_ready and postgres_ready
        status = "healthy" if configured else "degraded" if kafka_ready or postgres_ready else "inactive"
        layers.append(
            {
                "key": meta["key"],
                "label": meta["label"],
                "schema": meta["schema"],
                "status": status,
                "topicIn": topic_in,
                "topicOut": topic_out,
                "focus": meta["focus"],
            }
        )

    return layers


def _build_guardrails(templates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scope_limits = _load_scope_limits()
    return [
        {
            "key": "scope-pages",
            "label": "模板最大页数",
            "value": str(scope_limits["max_template_pages"]),
            "hint": "AI 生成模板时的分页上限",
            "status": "healthy",
        },
        {
            "key": "dry-run-limit",
            "label": "试跑样本上限",
            "value": str(scope_limits["max_dry_run_limit"]),
            "hint": "单次 dry-run 的样本限制",
            "status": "healthy",
        },
        {
            "key": "adapter-lines",
            "label": "适配器代码行数",
            "value": str(scope_limits["max_generated_adapter_lines"]),
            "hint": "生成代码的安全阈值",
            "status": "healthy",
        },
        {
            "key": "anti-crawl",
            "label": "反爬智能层",
            "value": "已启用" if settings.anti_crawl_enabled else "未启用",
            "hint": f"本地地址屏蔽 {scope_limits['blocked_exact_hosts']} 条",
            "status": "healthy" if settings.anti_crawl_enabled else "degraded",
        },
        {
            "key": "scheduler",
            "label": "增强调度器",
            "value": "已启用" if settings.scheduler_enabled else "未启用",
            "hint": "任务配置可沉淀为调度资产",
            "status": "healthy" if settings.scheduler_enabled else "degraded",
        },
        {
            "key": "template-domains",
            "label": "覆盖数据域",
            "value": str(len({template["dataType"] for template in templates})),
            "hint": "按模板声明的数据主题统计",
            "status": "healthy" if templates else "inactive",
        },
    ]


def _build_recommendations(
    templates: list[dict[str, Any]],
    stages: list[dict[str, Any]],
    task_board: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    recommendations: list[dict[str, Any]] = []

    if not templates:
        recommendations.append(
            {
                "title": "先生成首批采集模板",
                "detail": "当前模板目录为空，建议从 AI Collect 工作台创建首个源站模板。",
                "action": "进入智能采集",
                "path": "/ai-collect",
                "level": "critical",
            }
        )

    disabled_services = [stage for stage in stages if stage["status"] != "healthy"]
    if disabled_services:
        names = " / ".join(stage["title"] for stage in disabled_services[:3])
        recommendations.append(
            {
                "title": "补齐链路依赖配置",
                "detail": f"{names} 仍处于未完全就绪状态，建议检查 MongoDB、Kafka、MinIO、Postgres 配置。",
                "action": "查看运行监控",
                "path": "/monitor",
                "level": "warning",
            }
        )

    planned_count = sum(1 for task in task_board if task.get("kind") == "suggested")
    if planned_count:
        recommendations.append(
            {
                "title": "把模板转成可执行任务",
                "detail": f"已有 {planned_count} 个模板处于待发布状态，可以在任务中心绑定调度与目标表。",
                "action": "打开任务中心",
                "path": "/tasks",
                "level": "info",
            }
        )

    data_types = Counter(str(template.get("dataType", "other")) for template in templates)
    if data_types:
        most_common = data_types.most_common(1)[0][0]
        recommendations.append(
            {
                "title": "为高频数据域建立字段治理基线",
                "detail": f"{_DATA_TYPE_LABELS.get(most_common, most_common)} 模板数量最多，适合优先沉淀字段映射与质量规则。",
                "action": "查看字段识别",
                "path": "/field-mapping",
                "level": "info",
            }
        )

    return recommendations[:4]


async def build_platform_overview() -> dict[str, Any]:
    templates = _load_templates()
    source_groups = _build_source_groups(templates)
    stages = _build_stage_cards(templates)
    task_board = _build_task_board(templates)
    etl_layers = _build_etl_layers()
    guardrails = _build_guardrails(templates)
    recommendations = _build_recommendations(templates, stages, task_board)

    healthy_stage_count = sum(1 for stage in stages if stage["status"] == "healthy")
    health_score = min(98, 56 + healthy_stage_count * 11 + min(len(templates), 10))
    unique_domains = {template["domain"] for template in templates if template.get("domain")}
    live_task_count = sum(1 for task in task_board if task.get("kind") == "live")

    return {
        "updatedAt": _now_iso(),
        "summary": {
            "healthScore": health_score,
            "templateCount": len(templates),
            "sourceCount": len(unique_domains),
            "liveTaskCount": live_task_count,
            "dataDomainCount": len(source_groups),
            "healthyStageCount": healthy_stage_count,
        },
        "stages": stages,
        "sources": source_groups,
        "taskBoard": task_board,
        "etlLayers": etl_layers,
        "guardrails": guardrails,
        "recommendations": recommendations,
    }
