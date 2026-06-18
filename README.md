# Spider

Spider 是一个面向站点模板化采集的 Python + FastAPI + React 项目。当前架构围绕 `templates/` 中的 YAML 采集模板、`app/adapters/` 中的站点适配器，以及 MongoDB、MinIO、Kafka、PostgreSQL/TimescaleDB 组成的采集与处理链路展开。

## 当前架构

```text
templates/*.yaml
    |
    v
app.crawler.main
    |
    v
app.engine.SpiderEngine
    |-- app.engine.template_loader     加载 YAML 模板
    |-- app.engine.jinja2_renderer     渲染动态模板
    |-- app.downloader.http_client     发起 HTTP 请求
    |-- app.parser.template_parser     解析 HTML/JSON 响应
    |-- app.adapters.*                 注入站点特定逻辑
    |-- app.dedup                      Redis 去重
    |-- app.anti_crawl                 代理、延迟、身份轮换
    v
MongoDB / local output
    |
    v
app.downloader.main -> MinIO
    |
    v
app.syncer.main -> Kafka
    |
    v
app.etl.main
    |
    +-- RDS -> ODS -> TASK -> DWD -> DWS
    +-- DIM
    +-- ADS / downstream consumers

app.web.main
    |
    +-- REST API / WebSocket
    +-- web-panel React 管理界面
```

## 核心目录

```text
app/
  adapters/       站点适配器。通过 @register_adapter 注册，由模板 adapter 字段引用
  ai/             AI 辅助能力，如 SQL 提示、Dashboard 指标
  anti_crawl/     反爬能力，包括代理池、请求延迟、身份轮换与反爬数据源
  base/           Mongo、Kafka、MinIO、HTTP、Elasticsearch 等基础客户端封装
  config/         Pydantic 配置，统一读取 SPIDER_ 前缀环境变量
  crawler/        采集服务入口
  dedup/          Redis 去重
  downloader/     资源下载服务，将 MongoDB 中的资源落到 MinIO 或本地文件
  engine/         采集引擎、模板加载、工作流引擎、Jinja2 渲染
  etl/            Kafka + Postgres/TimescaleDB ETL Worker
  models/         模板与工作流模型
  parser/         模板化字段解析器
  quality/        数据校验、Schema、健康检查
  scheduler/      任务队列与请求兜底
  storage/        MongoDB、PostgreSQL、Kafka、MinIO、Elasticsearch、文件存储
  syncer/         MongoDB 到 Kafka 的同步服务
  tools/          批量采集、本地数据导入、参数生成等脚本
  web/            FastAPI 管理后端

templates/        YAML 采集模板
web-panel/        React + Vite 管理前端
docker/           各服务 Dockerfile
data/             批量采集参数与本地数据文件
```

## 主要服务

| 服务 | 入口 | 说明 |
| --- | --- | --- |
| Crawler | `python -m app.crawler.main` | 加载模板并执行采集 |
| Downloader | `python -m app.downloader.main` | 轮询 MongoDB，下载资源到 MinIO/本地 |
| Syncer | `python -m app.syncer.main` | 轮询 MongoDB，将已处理数据推送到 Kafka |
| ETL | `python -m app.etl.main --layer <layer>` | 消费 Kafka，写入 Postgres/TimescaleDB |
| Web API | `uvicorn app.web.main:app --host 0.0.0.0 --port 8000 --reload` | FastAPI 管理后端 |
| Web Panel | `npm run dev` in `web-panel/` | React 管理界面 |

## 基础设施

`docker-compose.yml` 中包含以下主要组件：

| 组件 | 默认端口 | 用途 |
| --- | --- | --- |
| MongoDB | `27017` | 原始采集数据与任务数据 |
| Redis | `6379` | 去重、缓存、队列辅助 |
| MinIO | `9000`, `9001` | PDF、图片等资源文件存储 |
| Kafka | `9092`, `29092` | 采集数据与 ETL 层间消息 |
| PostgreSQL/TimescaleDB | `5432` | ETL 结构化数据仓库 |
| pgAdmin | `5050` | PostgreSQL 管理 |
| RedisInsight | `5540` | Redis 管理 |
| Compass Web | `8081` | MongoDB 管理 |

## 快速开始

### 1. 准备环境

```bash
cp .env.example .env
pip install -r requirements.txt
```

前端开发需要 Node.js 18+：

```bash
cd web-panel
npm install
```

### 2. 启动基础设施

```bash
docker compose up -d mongodb redis minio zookeeper kafka postgres pgadmin
```

如需启动全部服务：

```bash
docker compose up -d
```

### 3. 运行采集

运行单个模板：

```bash
python -m app.crawler.main --template google_patent:keyword=satellite
```

按文本文件批量运行：

```bash
python -m app.crawler.main \
  --template-name google_patent \
  --list-file data/publication_numbers_20_24.txt \
  --list-param publication_number \
  --batch-size 100 \
  --delay 1.5
```

使用 JSON 参数文件：

```bash
python -m app.crawler.main --param-file params.json
```

### 4. 启动 Web API 与前端

```bash
uvicorn app.web.main:app --host 0.0.0.0 --port 8000 --reload
```

```bash
cd web-panel
npm run dev
```

访问：

- API 文档: `http://localhost:8000/docs`
- 健康检查: `http://localhost:8000/api/health`
- 前端开发服务: `http://localhost:5173`

## ETL

ETL Worker 依赖 Kafka 与 PostgreSQL/TimescaleDB。常用启动方式：

```bash
python -m app.etl.main --layer rds
python -m app.etl.main --layer ods
python -m app.etl.main --layer task
python -m app.etl.main --layer dwd
python -m app.etl.main --layer dws
python -m app.etl.main --layer dim
python -m app.etl.main --layer all
```

数据主题默认链路：

```text
spider-crawler
  -> spider-rds-processed
  -> spider-ods-processed
  -> spider-task-processed
  -> spider-dwd-processed
  -> spider-dws-processed
  -> spider-dim-processed
```

## Web API

核心路由挂载在 `/api` 下：

| 路由 | 说明 |
| --- | --- |
| `GET /api/health` | 基础设施健康检查 |
| `GET /api/dashboard/metrics` | Dashboard 指标 |
| `GET /api/dashboard/alerts` | Dashboard 告警 |
| `GET /api/templates` | 模板列表 |
| `GET /api/templates/{name}` | 模板详情 |
| `POST /api/templates` | 创建模板 |
| `PUT /api/templates/{name}` | 更新模板 |
| `DELETE /api/templates/{name}` | 删除模板 |
| `GET /api/tasks` | 任务列表 |
| `POST /api/tasks/run` | 运行采集任务 |
| `POST /api/tasks/schedule` | 调度采集任务 |
| `GET /api/etl/layers` | ETL 层级列表 |
| `GET /api/etl/{layer}/tables` | 指定层级下的数据表 |
| `GET /api/etl/{layer}/{table}/data` | 查询表数据 |
| `POST /api/etl/query` | 执行只读 SQL 查询 |
| `GET /api/etl/handlers/{layer}/{table}` | 查看 Handler |
| `PUT /api/etl/handlers/{layer}/{table}` | 更新 Handler |
| `POST /api/etl/handlers/{layer}/{table}/validate` | 校验 Handler |
| `WS /api/monitor/ws` | 实时监控 WebSocket |
| `GET /api/ai/analyze-stream` | AI 分析流 |
| `POST /api/ai/generate-template` | AI 生成模板 |
| `POST /api/ai/dry-run` | AI 试运行 |
| `POST /api/ai/generate-adapter` | AI 生成适配器 |

## 模板与适配器

采集能力由两部分组成：

1. `templates/*.yaml` 描述站点 URL、请求、字段映射、分页、下载资源、批量参数等。
2. `app/adapters/*.py` 处理站点特有行为，比如特殊请求头、翻页状态、批量参数拼接、错误处理。

新增模板时，建议只新增文件：

```yaml
name: my_site
display_name: My Site
base_url: https://example.com
data_type: article
adapter: my_site
response_type: html
list_page: /news?page={page}
list_fields:
  - name: title
    selector: h2
    selector_type: css
    field_type: text
list_pagination:
  type: page_number
  page_param: page
  start_page: 1
  max_pages: 10
```

新增适配器时，建议只新增文件：

```python
from __future__ import annotations

from app.adapters import BaseSiteAdapter, register_adapter


@register_adapter("my_site")
class MySiteAdapter(BaseSiteAdapter):
    adapter_name = "my_site"

    def on_request_headers(self, page: int) -> dict[str, str]:
        return {"Referer": self._base_url}
```

适配器会由 `app.adapters` 自动扫描导入，模板中的 `adapter: my_site` 会绑定到对应注册名。未找到适配器时会回退到 `GenericAdapter`。

## 配置

配置由 `app.config.settings` 读取，环境变量统一使用 `SPIDER_` 前缀。常用项：

```bash
SPIDER_LOG_LEVEL=INFO
SPIDER_TEMPLATE_DIR=templates
SPIDER_OUTPUT_DIR=output

SPIDER_DB_URL=mongodb://localhost:27017
SPIDER_DB_NAME=spider
SPIDER_REDIS_URL=redis://localhost:6379/0
SPIDER_MINIO_ENDPOINT=localhost:9000
SPIDER_KAFKA_BROKERS=localhost:9092
SPIDER_PG_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/spider_etl

SPIDER_MAX_CONCURRENT_TASKS=5
SPIDER_PAGE_CONCURRENCY=1
SPIDER_DEDUP_ENABLED=true
SPIDER_INCREMENTAL_MODE=true
SPIDER_ANTI_CRAWL_ENABLED=true
SPIDER_JINJA2_ENABLED=true
SPIDER_SCHEDULER_ENABLED=true
```

## 开发命令

```bash
make install
make install-dev
make lint
make format
make type-check
make test
make test-cov
make web
make etl-all
make docker-up
make docker-down
make docker-logs
```

注意：当前 Makefile 中部分采集命令仍引用旧入口 `app.main`。直接运行采集时优先使用：

```bash
python -m app.crawler.main --template <template_name>:<param>=<value>
```

## AI/Agent 修改约束

仓库根目录包含 `AGENTS.md`，用于约束所有 AI/agent 的代码修改范围。默认规则是：

- 只允许在 `templates/` 中新增模板。
- 只允许在 `app/adapters/` 中新增适配器。
- 不允许修改或删除其他文件，除非维护者针对具体任务给出明确授权。

本 README 的调整属于维护者明确授权的文档更新。
