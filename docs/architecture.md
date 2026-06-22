# 数据中台技术架构文档

> **项目代号**: Spider → DataHub  
> **文档版本**: v1.0  
> **创建日期**: 2026-06-22  
> **负责角色**: 技术负责人 / 架构师  
> **依赖文档**: [产品设计文档](./product-design.md)  
> **被依赖文档**: [实施路线图](./roadmap.md)

---

## 1. 架构设计原则

| 原则 | 说明 | 在 DataHub 中的体现 |
|------|------|-------------------|
| **演进优于重建** | 在现有系统上增量扩展，不推倒重来 | Spider 核心链路保留，新增模块以插件方式接入 |
| **分层解耦** | 各层职责单一，层间通过标准接口通信 | 采集层 → 存储层 → 治理层 → 服务层 → 应用层 |
| **配置驱动** | 用配置而非代码扩展能力 | YAML 模板、数据源配置、ETL handler 配置 |
| **可观测优先** | 每个环节可追踪、可监控、可告警 | 全链路日志 + 指标 + 告警 |
| **适度设计** | 避免过度工程，按需引入复杂度 | MVP 不引入 DolphinScheduler/Atlas，P2 评估 |

---

## 2. 整体架构图

```
┌─────────────────────────── DataHub 数据中台 ───────────────────────────┐
│                                                                        │
│  ┌─── 应用层 (Application Layer) ──────────────────────────────────┐  │
│  │  可视化看板    自助分析    报表中心    AI Copilot                 │  │
│  │  (Metabase)   (SQL IDE)   (定时生成)  (扩展 app/ai)              │  │
│  └──────────────────────────┬──────────────────────────────────────┘  │
│                             │                                          │
│  ┌─── 服务层 (Service Layer) ──────────────────────────────────────┐  │
│  │  数据 API 网关    指标平台    数据目录    数据共享                 │  │
│  │  (FastAPI)       (指标引擎)  (元数据)   (导出/订阅)              │  │
│  └──────────────────────────┬──────────────────────────────────────┘  │
│                             │                                          │
│  ┌─── 治理层 (Governance Layer) ───────────────────────────────────┐  │
│  │  数据质量    元数据管理    血缘追踪    权限管控    日志审计       │  │
│  │  (规则引擎)  (自动采集)    (SQL解析)  (RBAC)    (全链路)        │  │
│  └──────────────────────────┬──────────────────────────────────────┘  │
│                             │                                          │
│  ┌─── 存储层 (Storage Layer) ──────────────────────────────────────┐  │
│  │  RDS      ODS       DWD       DWS       ADS       DIM           │  │
│  │  (Mongo)  (PG分区)  (PG分区)  (PG物化)  (PG+Redis) (PG)         │  │
│  └──────────────────────────┬──────────────────────────────────────┘  │
│                             │                                          │
│  ┌─── 采集层 (Ingestion Layer) ────────────────────────────────────┐  │
│  │  爬虫采集      API 接入      文件上传      DB 同步               │  │
│  │  (现有engine)  (新adapter)  (新upload)   (SeaTunnel/自研)       │  │
│  └──────────────────────────┬──────────────────────────────────────┘  │
│                             │                                          │
│  ┌─── 管控层 (Control Layer) ──────────────────────────────────────┐  │
│  │  任务调度       监控告警       配置管理       日志收集           │  │
│  │  (DAG引擎)     (Prometheus)   (Consul/etcd)  (ELK)             │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘

                         数据流:
  数据源 → 采集层 → RDS → Kafka → ODS → DWD → DWS → ADS → 服务层 → 应用层
                                         ↑               │
                                        DIM              └→ 指标平台
```

---

## 3. 技术栈选型

### 3.1 现有技术栈（保留）

| 类别 | 技术 | 版本 | 用途 |
|------|------|------|------|
| 后端语言 | Python | 3.12+ | 所有后端逻辑 |
| Web 框架 | FastAPI | — | REST API |
| 前端框架 | React + Vite | — | Web 管理面板 |
| 关系数据库 | PostgreSQL 16 + TimescaleDB | — | 数仓分层存储 |
| 文档数据库 | MongoDB | — | 原始数据缓存 |
| 消息队列 | Kafka | — | 层间解耦缓冲 |
| 对象存储 | MinIO | — | 文件/资源存储 |
| 缓存 | Redis | — | 去重/缓存/限流 |
| 全文搜索 | Elasticsearch | — | 全文检索（可选） |
| 容器化 | Docker + Docker Compose | — | 部署 |

### 3.2 新增技术栈

| 类别 | 技术 | 引入阶段 | 用途 | 选型理由 |
|------|------|---------|------|---------|
| 任务调度 | 自研轻量 DAG (P0) → DolphinScheduler (P2评估) | P0 | 工作流编排 | MVP 需求简单，自研成本低；后期评估是否需要 DS 的多租户/告警 |
| 数据集成 | Apache SeaTunnel (评估) 或 自研 Python 同步 | P0 | DB 同步 | SeaTunnel 200+ 连接器，CDC 支持；若部署成本高则自研 |
| BI 引擎 | Metabase | P1 | 可视化看板 | 开源、易部署、支持多数据源、SQL 驱动 |
| 元数据管理 | 自研 + information_schema 自动采集 | P1 | 元数据管理 | Atlas 太重，MVP 用 PG 系统表 + 自建元数据表 |
| 血缘解析 | sqlglot (Python SQL 解析库) | P1 | SQL 血缘解析 | 轻量、纯 Python、支持多方言 |
| 监控 | Prometheus + Grafana | P1 | 指标监控 | 行业标准，生态丰富 |
| API 网关 | FastAPI 内置 + Redis 限流 | P0 | 数据 API | 复用现有 FastAPI，无需引入独立网关 |

### 3.3 技术选型决策记录

#### D01: 调度引擎 — 自研轻量 DAG vs DolphinScheduler

| 维度 | 自研轻量 DAG | DolphinScheduler |
|------|-------------|-----------------|
| 开发成本 | 2-3 人天 | 0（开箱即用） |
| 部署复杂度 | 无额外组件 | 需部署 Master/Worker/API/Alert 4 个服务 |
| 功能覆盖 | DAG 依赖 + 重试 + 告警（满足 MVP） | DAG + 多租户 + 资源池 + 告警 + 补数 |
| 运维成本 | 低 | 中 |
| 扩展性 | 受限 | 强 |

**决策**: P0 用自研轻量 DAG（~500 行 Python），P2 评估是否迁移到 DolphinScheduler。

#### D02: DB 同步 — SeaTunnel vs 自研

| 维度 | SeaTunnel | 自研 Python 同步 |
|------|-----------|----------------|
| 连接器数量 | 200+ | 需逐个实现 |
| CDC 支持 | ✅ 原生 | 需集成 Debezium |
| 部署成本 | 需要 JVM + SeaTunnel 集群 | 无额外组件 |
| 维护成本 | 中（需维护集群） | 低 |

**决策**: P0 先自研 PostgreSQL/MySQL 同步（覆盖 80% 场景），P1 评估引入 SeaTunnel。

#### D03: BI 引擎 — Metabase vs Superset vs 自研

| 维度 | Metabase | Superset | 自研 |
|------|---------|---------|------|
| 部署复杂度 | 低（单 JAR/容器） | 中（多组件） | — |
| 易用性 | 高（非技术友好） | 中（偏技术） | — |
| 定制性 | 中 | 高 | 最高 |
| 维护成本 | 低 | 中 | 高 |

**决策**: P1 集成 Metabase，通过 iframe 嵌入管理面板。

---

## 4. 模块详细设计

### 4.1 采集层 (Ingestion Layer)

#### 4.1.1 适配器架构

```
app/adapters/
├── base.py              # 适配器基类（已有）
├── registry.py          # 适配器注册表（已有）
├── crawler/
│   ├── satellite_today.py
│   ├── google_patent.py
│   └── ...              # 现有爬虫适配器（保留）
├── api_source.py        # 新增: API 数据源适配器
├── file_upload.py       # 新增: 文件上传适配器
└── db_sync.py           # 新增: DB 同步适配器
```

#### 4.1.2 适配器接口定义

```python
# app/adapters/base.py (扩展)

class BaseAdapter(ABC):
    """所有数据源适配器的基类"""

    @abstractmethod
    async def fetch(self, config: dict) -> Iterator[dict]:
        """拉取数据，返回迭代器"""
        ...

    @abstractmethod
    def validate_config(self, config: dict) -> bool:
        """验证适配器配置"""
        ...

    def normalize(self, raw_data: dict) -> dict:
        """数据标准化（可选覆盖）"""
        return raw_data

    def get_source_type(self) -> str:
        """返回数据源类型"""
        return self.__class__.__name__
```

#### 4.1.3 API 数据源适配器设计

```python
# app/adapters/api_source.py

class APISourceAdapter(BaseAdapter):
    """REST API 数据源适配器"""

    async def fetch(self, config: dict) -> Iterator[dict]:
        """
        config 示例:
        {
            "url": "https://api.example.com/data",
            "method": "GET",
            "headers": {"Authorization": "Bearer xxx"},
            "params": {"page": 1, "size": 100},
            "pagination": {
                "type": "offset",  # offset / cursor / link
                "page_field": "page",
                "size_field": "size",
                "total_field": "total"
            },
            "data_path": "data.items",  # JSONPath
            "schedule": "0 */6 * * *"  # cron 表达式
        }
        """
        ...
```

#### 4.1.4 DB 同步适配器设计

```python
# app/adapters/db_sync.py

class DBSyncAdapter(BaseAdapter):
    """数据库同步适配器"""

    async def fetch(self, config: dict) -> Iterator[dict]:
        """
        config 示例:
        {
            "source": {
                "type": "postgresql",  # postgresql / mysql
                "dsn": "postgresql://user:pass@host:5432/db",
                "table": "orders",
                "mode": "incremental",  # full / incremental
                "watermark": {
                    "column": "updated_at",
                    "last_value": "2026-06-01 00:00:00"
                }
            },
            "schedule": "0 2 * * *"
        }
        """
        ...
```

### 4.2 存储层 (Storage Layer)

#### 4.2.1 分层 Schema 设计

```sql
-- RDS 层（原始数据接入，保留现有）
-- 存储在 MongoDB + PostgreSQL

-- ODS 层（标准化，保留现有）
CREATE TABLE IF NOT EXISTS ts_ods.source_data (
    id BIGSERIAL PRIMARY KEY,
    source_type VARCHAR(50) NOT NULL,      -- crawler / api / file / db_sync
    source_id VARCHAR(100) NOT NULL,       -- 数据源标识
    raw_data JSONB NOT NULL,               -- 原始数据
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at TIMESTAMPTZ,
    status VARCHAR(20) DEFAULT 'pending'   -- pending / processed / error
) PARTITION BY RANGE (fetched_at);

-- DWD 层（明细数据，保留现有）
-- DWS 层（汇总数据，保留现有）

-- ADS 层（应用数据，新增）
CREATE TABLE IF NOT EXISTS ts_ads.app_metrics (
    id BIGSERIAL PRIMARY KEY,
    metric_code VARCHAR(200) NOT NULL,     -- 指标编码
    metric_name VARCHAR(500),              -- 指标名称
    dimension JSONB,                       -- 维度值
    value NUMERIC,                         -- 指标值
    period VARCHAR(20),                    -- 周期: day/week/month
    period_date DATE NOT NULL,             -- 统计日期
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (metric_code, dimension, period, period_date)
) PARTITION BY RANGE (period_date);

-- 元数据表（新增）
CREATE TABLE IF NOT EXISTS meta_tables (
    id BIGSERIAL PRIMARY KEY,
    schema_name VARCHAR(100) NOT NULL,
    table_name VARCHAR(200) NOT NULL,
    table_comment TEXT,
    layer VARCHAR(20),                     -- rds/ods/dwd/dws/ads/dim
    source_type VARCHAR(50),
    owner VARCHAR(100),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (schema_name, table_name)
);

CREATE TABLE IF NOT EXISTS meta_columns (
    id BIGSERIAL PRIMARY KEY,
    table_id BIGINT REFERENCES meta_tables(id) ON DELETE CASCADE,
    column_name VARCHAR(200) NOT NULL,
    data_type VARCHAR(100),
    nullable BOOLEAN DEFAULT TRUE,
    column_comment TEXT,
    is_primary_key BOOLEAN DEFAULT FALSE,
    ordinal INT NOT NULL,
    UNIQUE (table_id, column_name)
);

-- 血缘关系表（新增）
CREATE TABLE IF NOT EXISTS meta_lineage (
    id BIGSERIAL PRIMARY KEY,
    source_table VARCHAR(300) NOT NULL,    -- schema.table
    source_columns TEXT[],                 -- 源字段列表
    target_table VARCHAR(300) NOT NULL,
    target_columns TEXT[],                 -- 目标字段列表
    transform_sql TEXT,                    -- 转换 SQL
    etl_handler VARCHAR(200),              -- ETL handler 名称
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 数据质量规则表（新增）
CREATE TABLE IF NOT EXISTS quality_rules (
    id BIGSERIAL PRIMARY KEY,
    rule_name VARCHAR(200) NOT NULL,
    rule_type VARCHAR(50),                 -- completeness / accuracy / consistency / timeliness
    target_table VARCHAR(300) NOT NULL,
    target_column VARCHAR(200),
    rule_expression TEXT NOT NULL,         -- SQL 表达式
    threshold NUMERIC DEFAULT 1.0,         -- 合格率阈值
    severity VARCHAR(20) DEFAULT 'warning', -- warning / error
    enabled BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 数据质量检查结果表
CREATE TABLE IF NOT EXISTS quality_results (
    id BIGSERIAL PRIMARY KEY,
    rule_id BIGINT REFERENCES quality_rules(id),
    check_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    total_count BIGINT,
    pass_count BIGINT,
    fail_count BIGINT,
    pass_rate NUMERIC,
    status VARCHAR(20),                    -- passed / failed
    detail JSONB                           -- 失败样本
) PARTITION BY RANGE (check_time);
```

#### 4.2.2 Kafka Topic 规划

| Topic | 生产者 | 消费者 | 分区 | 保留 | 现有 |
|-------|--------|--------|------|------|------|
| `spider-rds-processed` | syncer | ETL ODS handler | 3 | 7d | ✅ |
| `spider-ods-processed` | ETL ODS handler | ETL TASK handler | 3 | 7d | ✅ |
| `spider-task-processed` | ETL TASK handler | ETL DWD handler | 3 | 7d | ✅ |
| `spider-dwd-processed` | ETL DWD handler | ETL DWS handler | 3 | 7d | ✅ |
| `spider-dws-processed` | ETL DWS handler | ETL ADS handler | 3 | 7d | ✅ |
| `spider-ads-processed` | ETL ADS handler | 数据 API / 指标平台 | 3 | 3d | ❌ 新增 |
| `spider-quality-alert` | 质量检查引擎 | 告警服务 | 1 | 3d | ❌ 新增 |
| `spider-lineage-event` | ETL handlers | 血缘解析服务 | 1 | 7d | ❌ 新增 |

### 4.3 管控层 (Control Layer)

#### 4.3.1 轻量 DAG 调度引擎设计

```python
# app/scheduler/dag.py

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional
import asyncio

class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    RETRYING = "retrying"

@dataclass
class Task:
    task_id: str
    handler: Callable
    dependencies: list[str] = field(default_factory=list)
    retry_count: int = 0
    max_retries: int = 3
    retry_delay: int = 60  # seconds
    timeout: int = 3600    # seconds
    status: TaskStatus = TaskStatus.PENDING
    error: Optional[str] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None

@dataclass
class DAG:
    dag_id: str
    tasks: dict[str, Task] = field(default_factory=dict)
    schedule: Optional[str] = None  # cron expression

    def add_task(self, task: Task):
        self.tasks[task.task_id] = task

    def validate(self) -> bool:
        """验证 DAG 无环且依赖完整"""
        ...

    async def run(self, context: dict):
        """按拓扑序执行所有任务"""
        ...

    def get_execution_order(self) -> list[list[str]]:
        """返回分层执行顺序（并行组）"""
        ...
```

```python
# app/scheduler/engine.py

class SchedulerEngine:
    """轻量 DAG 调度引擎"""

    def __init__(self):
        self.dags: dict[str, DAG] = {}
        self.running: dict[str, asyncio.Task] = {}

    def register_dag(self, dag: DAG):
        self.dags[dag.dag_id] = dag

    async def start(self):
        """启动调度循环"""
        while True:
            await self._check_and_run()
            await asyncio.sleep(30)  # 每 30 秒检查一次

    async def _check_and_run(self):
        """检查到期的 DAG 并执行"""
        for dag_id, dag in self.dags.items():
            if self._should_run(dag) and dag_id not in self.running:
                self.running[dag_id] = asyncio.create_task(
                    self._run_dag(dag)
                )

    async def _run_dag(self, dag: DAG):
        """执行 DAG，处理重试和告警"""
        try:
            await dag.run(context={})
        except Exception as e:
            await self._alert(dag, e)
        finally:
            del self.running[dag.dag_id]

    async def _alert(self, dag: DAG, error: Exception):
        """发送告警"""
        ...
```

#### 4.3.2 DAG 定义示例

```yaml
# config/dags/etl_pipeline.yaml
dag_id: etl_pipeline
schedule: "0 */6 * * *"  # 每 6 小时

tasks:
  - task_id: crawl_satellite
    handler: app.crawler.main:run_satellite
    timeout: 1800

  - task_id: crawl_patent
    handler: app.crawler.main:run_patent
    timeout: 1800

  - task_id: sync_rds
    handler: app.syncer.main:run_sync
    dependencies: [crawl_satellite, crawl_patent]
    timeout: 600

  - task_id: etl_ods
    handler: app.etl.handlers.ods:run
    dependencies: [sync_rds]
    timeout: 1200

  - task_id: etl_dwd
    handler: app.etl.handlers.dwd:run
    dependencies: [etl_ods]
    timeout: 1200

  - task_id: etl_dws
    handler: app.etl.handlers.dws:run
    dependencies: [etl_dwd]
    timeout: 1200

  - task_id: etl_ads
    handler: app.etl.handlers.ads:run
    dependencies: [etl_dws]
    timeout: 600

  - task_id: quality_check
    handler: app.quality.engine:run_checks
    dependencies: [etl_dws]
    timeout: 300

  - task_id: refresh_api_cache
    handler: app.web.api_cache:refresh
    dependencies: [etl_ads]
    timeout: 120
```

### 4.4 服务层 (Service Layer)

#### 4.4.1 数据 API 设计

```python
# app/web/routes/data_api.py

from fastapi import APIRouter, Query, HTTPException
from typing import Optional

router = APIRouter(prefix="/api/v1/data", tags=["data-api"])

@router.get("/{table_name}")
async def query_table(
    table_name: str,
    select: str = Query("*", description="字段列表，逗号分隔"),
    where: Optional[str] = Query(None, description="WHERE 条件"),
    group_by: Optional[str] = Query(None, description="GROUP BY 字段"),
    order_by: Optional[str] = Query(None, description="ORDER BY 字段"),
    limit: int = Query(100, le=10000),
    offset: int = Query(0, ge=0),
    format: str = Query("json", regex="^(json|csv)$")
):
    """
    通用数据查询 API
    自动路由到对应分层表
    """
    ...

@router.post("/sql")
async def execute_sql(
    sql: str,
    timeout: int = 30
):
    """SQL 工作台（P1 阶段实现，需权限检查）"""
    ...

@router.get("/metadata/tables")
async def list_tables(
    layer: Optional[str] = Query(None, regex="^(rds|ods|dwd|dws|ads|dim)$")
):
    """列出所有表（支持按分层过滤）"""
    ...

@router.get("/metadata/tables/{table_name}/schema")
async def get_table_schema(table_name: str):
    """获取表结构"""
    ...

@router.get("/lineage/{table_name}")
async def get_lineage(table_name: str):
    """获取表血缘（P1 阶段实现）"""
    ...
```

#### 4.4.2 API 限流策略

```python
# app/web/middleware/rate_limit.py

# 基于 Redis 的滑动窗口限流
# 默认: 每用户 60 次/分钟, 每 IP 200 次/分钟
# 数据 API: 每用户 30 次/分钟（查询较重）
# SQL 工作台: 每用户 10 次/分钟

RATE_LIMITS = {
    "/api/v1/data/{table_name}": {"user": 30, "ip": 100, "window": 60},
    "/api/v1/data/sql": {"user": 10, "ip": 30, "window": 60},
    "default": {"user": 60, "ip": 200, "window": 60},
}
```

### 4.5 治理层 (Governance Layer)

#### 4.5.1 元数据自动采集

```python
# app/governance/metadata_collector.py

class MetadataCollector:
    """从 PostgreSQL information_schema 自动采集元数据"""

    async def collect_all(self):
        """采集所有分层表的元数据"""
        schemas = ["ts_rds", "ts_ods", "ts_task", "ts_dwd", "ts_dws", "ts_ads", "ts_dim"]
        for schema in schemas:
            await self._collect_schema_tables(schema)

    async def _collect_schema_tables(self, schema: str):
        """采集指定 schema 的所有表结构"""
        sql = """
            SELECT
                t.table_name,
                pgd.description as table_comment,
                c.column_name,
                c.data_type,
                c.is_nullable,
                c.ordinal_position,
                pg_catalog.col_description(
                    c.table_name::regclass, c.ordinal_position
                ) as column_comment,
                CASE WHEN pk.column_name IS NOT NULL THEN true ELSE false END as is_primary_key
            FROM information_schema.tables t
            LEFT JOIN information_schema.columns c ON t.table_name = c.table_name
            LEFT JOIN pg_description pgd ON pgd.objoid = t.table_name::regclass
            LEFT JOIN information_schema.key_column_usage pk
                ON pk.table_name = c.table_name AND pk.column_name = c.column_name
            WHERE t.table_schema = %s AND t.table_type = 'BASE TABLE'
            ORDER BY t.table_name, c.ordinal_position
        """
        ...
```

#### 4.5.2 血缘追踪设计

```python
# app/governance/lineage_parser.py

import sqlglot
from sqlglot import exp

class LineageParser:
    """基于 sqlglot 解析 SQL 生成血缘"""

    def parse(self, sql: str, target_table: str) -> dict:
        """
        解析 SQL，提取源表/字段 → 目标表/字段的映射

        示例:
            sql = "INSERT INTO ts_dwd.satellite_detail
                   SELECT s.id, s.name, d.country
                   FROM ts_ods.satellite_raw s
                   JOIN ts_dim.country d ON s.country_id = d.id"

            return:
            {
                "target_table": "ts_dwd.satellite_detail",
                "sources": [
                    {
                        "table": "ts_ods.satellite_raw",
                        "columns": ["id", "name"]
                    },
                    {
                        "table": "ts_dim.country",
                        "columns": ["country", "id"]
                    }
                ],
                "column_mapping": [
                    {"target": "id", "source": "s.id"},
                    {"target": "name", "source": "s.name"},
                    {"target": "country", "source": "d.country"}
                ]
            }
        """
        ast = sqlglot.parse(sql)
        ...
```

#### 4.5.3 数据质量引擎

```python
# app/governance/quality_engine.py

class QualityEngine:
    """数据质量检查引擎"""

    QUALITY_TEMPLATES = {
        "completeness": "SELECT COUNT(*) AS total, COUNT({column}) AS non_null FROM {table}",
        "uniqueness": "SELECT COUNT(*) AS total, COUNT(DISTINCT {column}) AS unique_count FROM {table}",
        "validity": "SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE {expression}) AS valid FROM {table}",
        "timeliness": "SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE {column} >= NOW() - INTERVAL '{days} days') AS fresh FROM {table}",
    }

    async def run_check(self, rule_id: int) -> dict:
        """执行单条质量规则"""
        rule = await self._get_rule(rule_id)
        sql = self._build_check_sql(rule)
        result = await self._execute(sql)
        pass_rate = result['pass_count'] / result['total_count'] if result['total_count'] > 0 else 1.0
        return {
            "rule_id": rule_id,
            "pass_rate": pass_rate,
            "status": "passed" if pass_rate >= rule['threshold'] else "failed",
            "detail": result
        }

    async def run_all_checks(self, table_name: str = None):
        """执行指定表（或全部）的质量规则"""
        ...
```

---

## 5. 目录结构规划

```
clawer/                          # Spider 仓库根目录（保留）
├── app/
│   ├── adapters/                # 数据源适配器（扩展）
│   │   ├── base.py
│   │   ├── registry.py
│   │   ├── crawler/             # 现有爬虫适配器
│   │   ├── api_source.py        # 新增: API 源
│   │   ├── file_upload.py       # 新增: 文件上传
│   │   └── db_sync.py           # 新增: DB 同步
│   ├── crawler/                 # 爬虫核心（保留）
│   ├── engine/                  # 模板引擎（保留）
│   ├── anti_crawl/              # 反爬模块（保留）
│   ├── downloader/              # 下载器（保留）
│   ├── syncer/                  # 同步模块（扩展）
│   │   ├── base.py
│   │   └── db_sync.py           # 新增: DB 同步
│   ├── etl/                     # ETL 模块（扩展）
│   │   ├── base.py
│   │   ├── handlers/
│   │   │   ├── ods.py
│   │   │   ├── task.py
│   │   │   ├── dwd.py
│   │   │   ├── dws.py
│   │   │   └── ads.py           # 新增: ADS handler
│   │   └── schema.py
│   ├── governance/              # 数据治理（新增）
│   │   ├── __init__.py
│   │   ├── metadata_collector.py
│   │   ├── lineage_parser.py
│   │   └── quality_engine.py
│   ├── scheduler/               # 任务调度（重写）
│   │   ├── __init__.py
│   │   ├── dag.py
│   │   ├── engine.py
│   │   └── handlers.py
│   ├── storage/                 # 存储管理（扩展）
│   │   ├── base.py
│   │   └── lifecycle.py         # 新增: 冷热分层
│   ├── ai/                      # AI 模块（扩展）
│   ├── web/                     # Web 服务（扩展）
│   │   ├── app.py
│   │   ├── routes/
│   │   │   ├── data_api.py      # 新增: 数据 API
│   │   │   ├── metadata.py      # 新增: 元数据 API
│   │   │   ├── quality.py       # 新增: 质量 API
│   │   │   ├── lineage.py       # 新增: 血缘 API
│   │   │   ├── upload.py        # 新增: 文件上传
│   │   │   └── scheduler.py     # 新增: 调度管理
│   │   ├── middleware/
│   │   │   └── rate_limit.py    # 新增: 限流
│   │   └── api_cache.py         # 新增: API 缓存
│   ├── quality/                 # 质量检查（现有，保留）
│   ├── config/                  # 配置（扩展）
│   │   └── dags/                # 新增: DAG 定义目录
│   └── utils/                   # 工具（保留）
├── web-panel/                   # 前端面板（扩展）
│   ├── src/
│   │   ├── pages/
│   │   │   ├── Dashboard.vue    # 现有
│   │   │   ├── DataCatalog.vue  # 新增: 数据目录
│   │   │   ├── DataQuality.vue  # 新增: 数据质量
│   │   │   ├── Lineage.vue      # 新增: 血缘图
│   │   │   ├── DataAPI.vue      # 新增: 数据 API 管理
│   │   │   ├── Scheduler.vue    # 新增: 调度管理
│   │   │   ├── SQLWorkbench.vue # 新增: SQL 工作台
│   │   │   └── FileUpload.vue   # 新增: 文件上传
│   │   └── components/
├── config/
│   ├── adapters/                # 适配器配置
│   ├── dags/                    # DAG 配置
│   └── quality/                 # 质量规则配置
├── docs/                        # 文档
│   ├── product-design.md        # 产品设计文档
│   ├── architecture.md          # 技术架构文档（本文档）
│   └── roadmap.md               # 实施路线图
├── scripts/
│   ├── migrate_ads.sql          # ADS 层建表脚本
│   ├── migrate_meta.sql         # 元数据表建表脚本
│   └── init_dag.py              # DAG 初始化脚本
├── tests/
│   ├── test_adapters/
│   ├── test_governance/
│   └── test_scheduler/
├── docker-compose.yml           # 现有
├── Dockerfile                   # 现有
├── pyproject.toml               # 现有
└── README.md                    # 更新
```

---

## 6. 部署架构

### 6.1 MVP 部署架构

```
┌───────────────────────────────────────┐
│           Docker Compose              │
│                                       │
│  ┌─────────┐  ┌─────────┐  ┌───────┐│
│  │ FastAPI  │  │ React   │  │ Redis ││
│  │ (Web+API)│  │ (Nginx) │  │       ││
│  └────┬─────┘  └────┬────┘  └───┬───┘│
│       │              │           │    │
│  ┌────▼──────────────▼───────────▼──┐│
│  │          PostgreSQL 16            ││
│  │          + TimescaleDB            ││
│  └────────────────┬─────────────────┘│
│                   │                   │
│  ┌───────┐  ┌────▼───┐  ┌─────────┐ │
│  │MongoDB│  │ Kafka  │  │ MinIO   │ │
│  └───────┘  └────────┘  └─────────┘ │
│                                       │
│  ┌──────────────────────────────────┐│
│  │    Scheduler (Python asyncio)    ││
│  │    (FastAPI 进程内)               ││
│  └──────────────────────────────────┘│
└───────────────────────────────────────┘
```

### 6.2 组件资源规划（MVP）

| 组件 | CPU | 内存 | 磁盘 | 说明 |
|------|-----|------|------|------|
| FastAPI (Web + API + Scheduler) | 2 核 | 2G | — | 单进程，asyncio 调度 |
| React (Nginx) | 0.5 核 | 256M | — | 静态文件 |
| PostgreSQL 16 | 2 核 | 4G | 100G SSD | 主存储 |
| MongoDB | 1 核 | 1G | 50G | 原始数据缓存 |
| Kafka | 1 核 | 1G | 50G | 消息队列 |
| Redis | 0.5 核 | 512M | — | 缓存 |
| MinIO | 0.5 核 | 512M | 200G | 文件存储 |
| **合计** | **7.5 核** | **9.3G** | **400G** | — |

### 6.3 P1 扩展部署

新增组件：
| 组件 | CPU | 内存 | 磁盘 | 说明 |
|------|-----|------|------|------|
| Metabase | 1 核 | 1G | — | BI 引擎 |
| Prometheus | 0.5 核 | 512M | 20G | 指标采集 |
| Grafana | 0.5 核 | 512M | — | 指标可视化 |

---

## 7. 安全设计

### 7.1 认证与授权

| 层级 | 方案 | 阶段 |
|------|------|------|
| API 认证 | JWT Token（FastAPI 内置） | P0 |
| 表级权限 | PostgreSQL Role + Schema 权限 | P0 |
| 行列级权限 | RLS (Row Level Security) + 视图 | P2 |
| API 鉴权 | RBAC 中间件 | P0 |
| 数据脱敏 | 视图 + 动态脱敏函数 | P2 |

### 7.2 数据安全

| 措施 | 说明 | 阶段 |
|------|------|------|
| 传输加密 | HTTPS (Nginx TLS) | P0 |
| 存储加密 | PG 透明数据加密 (TDE) | P2 |
| 敏感数据识别 | 正则 + 字典匹配 | P1 |
| 审计日志 | 全链路操作日志 | P2 |
| 备份策略 | PG pg_dump + MinIO 版本 | P0 |

---

## 8. 性能设计

### 8.1 性能指标目标

| 指标 | 目标值 | 说明 |
|------|--------|------|
| 数据 API 查询延迟 (P95) | < 500ms | ADS/DWS 层查询 |
| 数据 API 查询延迟 (P99) | < 2s | 包含复杂聚合 |
| ETL 单层处理吞吐 | > 10K rows/s | ODS → DWD |
| 任务调度精度 | < 1min 延迟 | cron 触发到实际执行 |
| 管理面板加载 | < 2s | 首页首屏 |
| 元数据采集耗时 | < 30s | 全量表结构采集 |

### 8.2 优化策略

| 策略 | 适用场景 | 实现方式 |
|------|---------|---------|
| 分区表 | ODS/DWD/ADS 时序数据 | PG RANGE 分区（已有，扩展到 ADS） |
| 物化视图 | DWS 汇总数据 | PG 物化视图（已有） |
| 查询缓存 | 数据 API 重复查询 | Redis 缓存 + TTL |
| 读写分离 | 高并发查询场景 | PG 流复制 + 读副本（P2） |
| 连接池 | 数据库连接管理 | asyncpg 连接池 |
| 批量写入 | ETL 批量处理 | COPY 命令替代 INSERT |

---

## 9. 可观测性设计

### 9.1 日志体系

| 日志类型 | 格式 | 存储 | 保留 | 阶段 |
|---------|------|------|------|------|
| 应用日志 | JSON 结构化 | 文件 + ELK | 30d | P0 |
| 访问日志 | Nginx log | 文件 | 7d | P0 |
| 审计日志 | JSON 结构化 | PostgreSQL | 180d | P2 |
| 任务日志 | JSON 结构化 | PostgreSQL | 30d | P0 |

### 9.2 指标监控

| 指标类别 | 具体指标 | 采集方式 | 阶段 |
|---------|---------|---------|------|
| 系统指标 | CPU/Memory/Disk/Network | node_exporter | P1 |
| 应用指标 | API QPS/延迟/错误率 | FastAPI middleware | P0 |
| 数据指标 | 各层记录数/新鲜度/质量分 | 自研采集 | P1 |
| 任务指标 | DAG 执行时间/成功率/重试次数 | 调度引擎记录 | P0 |

### 9.3 告警规则

| 告警 | 条件 | 通知方式 | 阶段 |
|------|------|---------|------|
| 任务失败 | DAG 执行失败 | Webhook + Email | P0 |
| 数据质量 | 质量检查不合格率 > 阈值 | Webhook | P1 |
| API 异常 | 错误率 > 5% / 5min | Webhook | P0 |
| 存储告警 | 磁盘使用 > 80% | Email | P0 |
| 延迟告警 | API P95 > 1s / 5min | Webhook | P1 |

---

## 附录: 技术风险评估

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| 自研 DAG 调度功能不足 | 中 | 中 | 预留 DolphinScheduler 迁移接口，P2 评估 |
| SeaTunnel 集成成本高 | 中 | 低 | 备选方案: 自研 Python DB 同步 |
| 数据量增长导致 PG 性能下降 | 低 | 高 | 分区策略 + 冷热分层 + 读写分离 |
| 血缘解析覆盖不全 | 中 | 低 | 支持手动维护血缘 + 逐步增强 SQL 解析 |
| Metabase 定制性不足 | 低 | 低 | 保留自研看板作为备选 |
| Kafka 消息积压 | 低 | 中 | 监控 lag + 消费者扩容 + 死信队列 |

---

> **下一步**: 实施路线图 [roadmap.md](./roadmap.md) 将基于本架构文档定义分阶段交付计划和里程碑。
