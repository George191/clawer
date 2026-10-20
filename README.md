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
  base/           Kafka 连接配置、HTTP 响应契约及数据库客户端入口
  config/         Pydantic 配置，统一读取 SPIDER_ 前缀环境变量
  crawler/        采集服务入口
  dedup/          Redis 去重
  downloader/     资源下载服务，将 MongoDB 中的资源落到 MinIO 或本地文件
  engine/         采集引擎、模板加载、工作流引擎、Jinja2 渲染
  etl/            ETL Worker、Schema 管理、消费状态及数据转换规则
  models/         模板与工作流模型
  parser/         模板化字段解析器
  quality/        数据校验、Schema、健康检查
  records/        记录仓储、来源适配、集合路由与资源对象路径；供采集、下载、同步共用
  scheduler/      任务队列与请求兜底
  storage/        File、Kafka、MinIO、MongoDB、PostgreSQL、Redis 的连接客户端
  syncer/         MongoDB 到 Kafka 的同步服务
  tools/          批量采集、本地数据导入、参数生成等脚本
  web/            FastAPI 管理后端

templates/        YAML 采集模板
web-panel/        React + Vite 管理前端
docker/           各服务 Dockerfile
data/             批量采集参数与本地数据文件
```

`storage` 模块统一使用 `*_client.py` 和 `*Client` 命名，只负责连接、通用 I/O、重试与关闭。
记录去重、合并、下载和同步状态属于 `records`；`records/adapters.py` 在保存记录时使用，
与 `downloader/adapters` 的资源提取、响应校验职责分开。依赖方向为业务模块 → 客户端 → 驱动，
客户端不导入业务模块。

ETL 按职责组织：`etl/schema/layout.py` 定义表布局与分区规则，`baselines.py` 保存基线 DDL，
`ddl.py` 负责纯 DDL 生成和校验和计算，`registry.py` 通过 PostgreSQL 客户端维护注册表。
`etl/state/offsets.py` 通过 Redis 客户端管理消费位点与恢复状态；`normalizers/` 和 `tasks/`
分别承担标准化和业务处理。Schema 的纯规则模块不依赖数据库客户端，Worker 组合这些能力。

`base` 按组件命名（如 `kafka.py`、`redis.py`），只提供基础能力，不导出业务仓储。
Kafka 连接配置统一位于 `base/kafka.py`，业务数据类型列表位于 `records/types.py`。
HTTP 响应和异常契约位于 `base/http.py`，含反爬策略的请求实现位于 `downloader/http_client.py`。

目录迁移也需要同步更新 MinIO 中已发布适配器的 Python 导入。例如，原
`app.storage.mongo_storage.MongoStorage` 对应 `app.records.mongo_store.MongoRecordStore`；
Redis 统一使用 `app.base.redis.RedisClient` 或 `app.storage.redis_client.RedisClient`。
单独部署应用不会修改已发布适配器源码；旧的 `app.base` 业务仓储别名和
`app.base.redis_connection.RedisConnection` 导入也需随适配器一起迁移发布。

## 主要服务

| 服务 | 入口 | 说明 |
| --- | --- | --- |
| Crawler | `python -m app.crawler.main` | 加载模板并执行采集 |
| Downloader | `python -m app.downloader.main` | 轮询 MongoDB，下载资源到 MinIO/本地 |
| Syncer | `python -m app.syncer.main` | 轮询 MongoDB，将已处理数据推送到 Kafka |
| ETL | `python -m app.etl.main --layer <layer>` | 消费 Kafka，写入 Postgres/TimescaleDB |
| Web API | `uvicorn app.web.main:app --host 0.0.0.0 --port 8000 --reload` | FastAPI 管理后端 |
| Web Panel | `npm run dev` in `web-panel/` | React 管理界面 |

下载器按 PostgreSQL 模板分组并关联 `public.ai_collect_template_alias`，以别名映射的业务 `data_type` 筛选实际集合；未配置别名映射的模板使用原 `data_type` 和同名集合。匹配集合的待下载记录合并成一批（总数不超过 `--batch`）统一下载，每批及空闲轮询时重读数据库。不传 `--datatype` 时保留原行为，也可与 `--template` 同用；指定类型时，`--template` 支持父模板名和实际集合名：

```bash
python -m app.downloader.main --datatype news
python -m app.downloader.main --template ssc_news --datatype=news
python -m app.downloader.main --template sec_edgar --datatype filing
```

下载并发使用 `SPIDER_DOWNLOAD_ASSET_CONCURRENCY`（默认 64），实际同时处理记录数不超过 `--batch`；每条记录内的资源依次下载。仅领取 `pending` 记录，`downloading` 不按时间自动重新领取；遗留下载状态须在确认原下载进程停止后修复。记录结束时释放对应的 HTTP 会话和代理租约。

## 外部基础设施

MongoDB、Redis、MinIO、Kafka 和 PostgreSQL/TimescaleDB 由外部环境提供，
`docker-compose.yml` 只启动项目应用服务。所有连接地址、认证信息、Topic
和 Consumer Group 均从 `.env` 读取，Compose 不覆盖这些值。

| 组件 | 常用端口 | 用途 |
| --- | --- | --- |
| MongoDB | `27017` | 原始采集数据与任务数据 |
| Redis | `6379` | 去重、缓存、队列辅助 |
| MinIO | `9000`, `9001` | PDF、图片等资源文件存储 |
| Kafka | `9092`, `29092` | 采集数据与 ETL 层间消息 |
| PostgreSQL/TimescaleDB | `5432` | ETL 结构化数据仓库 |

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

### 2. 启动应用服务

首次部署或升级 AI Collect 数据库时，先在应用停止接收任务的维护窗口执行：

```bash
python -m scripts.init_ai_collect --apply
```

该命令负责建表、历史字段迁移和旧状态修正。需要注册本地 YAML 模板时，显式追加
`--import-templates`。已有数据库不需要在日常启动或每轮采集时重复执行；Web 和 Worker
只建立数据库连接，不自动建表、迁移或导入模板。容器部署可在项目根目录通过
`docker compose run --rm --no-deps -v "$PWD/scripts:/app/scripts:ro" crawler python -m scripts.init_ai_collect --apply`
执行相同迁移。

```bash
docker compose up -d
```

服务器部署会同时构建前端和后端，并通过 Nginx 的 `${SPIDER_HTTP_PORT:-80}`
提供入口。Celery Beat 独立保持单实例，Crawler Worker 可以横向扩容：

```bash
docker compose up -d --build
docker compose ps
curl http://localhost:${SPIDER_HTTP_PORT:-80}/api/health
```

Worker 总任务并发数为
`SPIDER_CELERY_CONCURRENCY * SPIDER_CELERY_WORKER_REPLICAS`。例如 30 并发：

```dotenv
SPIDER_CELERY_CONCURRENCY=5
SPIDER_CELERY_WORKER_REPLICAS=6
```

Compose 仅开放 Nginx 入口；生产环境仍应配置 HTTPS。启动前必须确保
容器网络能够访问 `.env` 中配置的所有外部服务地址。

Twitter 使用 twscrape 时，构建服务器需先准备已导入登录会话的
`data/twitter_accounts.db`。Crawler 镜像会将它复制到 `/app/data/twitter_accounts.db`，
并明确设置为容器内 `appuser` 可读写，无需挂载。该文件仍不纳入 Git；含登录会话的镜像应仅供内部使用。
更换会话后需重新构建并部署 crawler；容器重建会恢复镜像内的会话库初始状态。

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

## Syncer

Compose 为 news、patent、navwarn、intelligence、company、financial_fact、filing、account、tweet
分别启动独立 Syncer 容器。原 `syncer` 服务负责 news，其余服务为 `syncer-<类型>`（服务名中的下划线改为连字符）。
每批从 MongoDB 读取 50 条，进程内最多 10 个协程同时发送及回写状态；Kafka 中每条记录仍是独立消息。
仅发送成功后标记对应版本为 synced，失败记录保留待重试。
采集记录落库即标记为 pending，Syncer 查询不依赖下载状态；每条记录下载处理完成后立即原子回写资源、下载状态、更新时间并重置为 pending，不等待同批其他记录。下载回写同时递增 download_version；同步确认匹配发送时的 updated_at 和下载版本，避免同一毫秒内的下载更新被旧同步确认覆盖。
同步由轮询触发：新采集记录和下载更新均在下一次轮询发送，空闲轮询默认间隔 10 秒，可通过 `--poll` 调整。
指定类型时，与下载器共用模板分组及 `ai_collect_template_alias` 别名映射查询，只读取匹配业务类型的实际集合；MongoDB 仅按原同步状态条件查询，不增加 `_meta.data_type` 过滤，发送前在内存中校验记录类型，不再用启动参数覆盖错误类型。

模板与集合的别名映射由 `app/records/collection_routes.py` 查询 `public.ai_collect_template_alias`，供采集、下载和同步共用。表结构由部署迁移维护，业务映射单独配置。映射以 `template_name` 关联已注册模板，`data_type` 表示该集合的业务类型，`collection_name` 表示实际 MongoDB 集合，`enabled` 控制按类型运行的下载和同步启停，不影响采集写入。模板存在映射时不再回退到同名集合，禁用映射也不会触发回退。
模板版本沿用 active 优先、更新时间倒序的选择规则；先关联路由再筛选业务类型，因此 SEC 父模板的类型无需改成 company 或 filing。修改路由在下一批生效，已读入的当前批次继续完成。
模板的 `download[].collection` 使用实际集合名，变更实际集合时应一并更新对应下载配置。

例如禁止按类型运行的下载器与 Syncer 处理 SEC 财报集合：

```sql
UPDATE public.ai_collect_template_alias
SET enabled = false, updated_at = now()
WHERE template_name = 'sec_edgar' AND data_type = 'filing';
```

重新启用时将 `enabled` 设为 `true`。新增业务类型仍需补充 Syncer 参数白名单及下游处理器。

```bash
python -m app.syncer.main --data-type financial_fact --batch 50 --concurrency 10
```

指定类型后发送到 `${SPIDER_KAFKA_TOPIC}-<类型>`，例如 `spider-crawler-financial_fact`。
不传 `--data-type` 时扫描全部类型，按每条记录的 `_meta.data_type` 自动发送到 `${SPIDER_KAFKA_TOPIC}-<类型>`，例如 company 和 filing 分别发送到 `-company` 和 `-filing`；缺少类型且无法从已有 Twitter 集合映射确定类型的记录保留待处理，不发送到公共 topic。
同一范围只运行一个 Syncer 实例，勿同时运行全类型旧实例和新的分类型实例，以免重复发送。

切换前创建上述类型 topic（分区及副本数按集群配置），停止旧 Syncer，先更新 RDS Worker，再启动分类型 Syncer。
RDS 为旧 `SPIDER_ETL_RAW_TOPIC` 和各新类型 topic 分别启动独立 Consumer 消费循环，通过协程并发处理，沿用原消费组与 offset。

SAM 两个 MongoDB 集合分别写入 `ts_rds.rds_contract`（`sam_gov_awards`）和
`ts_rds.rds_tender`（`sam_gov_opportunities`），标准化后对应 `ts_ods.ods_contract` 和
`ts_ods.ods_tender`。合同采集与同步使用 `contract`，采购公告使用独立的 `tender` 类型，按类型选择 RDS 表，保留 Mongo
记录身份及合同修订/交易号；ODS 金额使用 `NUMERIC`，复杂字段使用 `JSONB`，删除使用标志保留。
来源时间同时保留原文；未声明时区的时间不推定为 UTC。`created_at`/`updated_at` 为入库时间。
使用 `python -m app.etl.schema.register_procurement` 查看四张表的 DDL，追加 `--apply` 注册并建表。
部署时先创建 `${SPIDER_KAFKA_TOPIC}-contract`、`${SPIDER_KAFKA_TOPIC}-tender`、
`${SPIDER_ETL_RDS_TOPIC}-contract` 和 `${SPIDER_ETL_RDS_TOPIC}-tender` topic，
再更新 ODS、RDS、`syncer-contract` 和 `syncer-tender` 服务。
从旧版本切换前，停止 SAM 采购公告采集与同步，用旧版 RDS/ODS 排空其在途 `contract` 消息。
将已发布 `sam_gov_opportunities` 的 MinIO YAML、`public.ai_collect_templates.data_type`，
以及该集合的 `public.ai_collect_template_alias.data_type`（如有）统一改为 `tender`；
MongoDB 已有记录的 `_meta.data_type` 也需改为 `tender`，保留记录身份和同步状态。
已有 `rds_tender` 行需同步修改 `data_type` 和 `raw_data._meta.data_type`；
若同一记录同时存在 `contract`、`tender` 两种主键，先核对并合并，避免类型切换后重复。
完成类型迁移后再启动新版服务；`ods_tender` 原有记录已经使用 `tender` 类型。
SAM 日增需在完整采集、写入及水位验证通过后注册到 `public.automation_tasks`；合同还需
Worker 可用的 `SAM_GOV_API_KEY` 以及完整变更/删除流验证，公告快照中缺失的记录不推断为删除。

USAspending 合同模板为 `usaspending`，使用免 Key 的官方批量 CSV 导出，按 FPDS
`last_modified_date` 获取 A/B/C/D 主合同及修订交易，默认重叠回扫 7 天、截至 UTC 昨日。
下载的 ZIP/CSV 在内存中校验及分批保存，完整保留原字段；交易唯一键决定身份，
独立来源写入现有 `rds_contract` / `ods_contract`，不覆盖 SAM 记录。
此模板不包含 IDV、分包、补助或合同附件；源修改时间不同于 USAspending 入库时间，
超过回扫窗口的迟到数据需按历史修改日期补抓。启用同步前须部署新的记录规则和 ODS normalizer。

各 topic 内仍按顺序逐条处理、成功后提交，数据库连接池由 RDS 统一管理。
RDS 按实际目标表输出到 `${SPIDER_ETL_RDS_TOPIC}-<表名后缀>`，例如 `rds_news` 对应 `etl-rds-news`、`rds_financial_fact` 对应 `etl-rds-financial_fact`（以配置的 topic 前缀为准）。
上线前创建这些 RDS 输出 topic，并先更新 ODS 再更新 RDS；ODS 兼容订阅旧公共 topic 与新拆分 topic，本次未改变 ODS 的串行处理及公共输出。
切换时先排空旧输入 topic，再开放新 Syncer，避免新旧 topic 的同一记录并发写入导致旧版本覆盖。
Syncer 需要重建 `docker/syncer.Dockerfile` 镜像，RDS 需要重建 `docker/etl.Dockerfile` 镜像。

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

数据主题链路（以实际 topic 配置为准）：

```text
spider-crawler-news           -> RDS -> etl-rds-news           ┐
spider-crawler-financial_fact -> RDS -> etl-rds-financial_fact ├-> ODS -> etl-ods
spider-crawler（旧 topic）     -> RDS -> 按实际写入表拆分输出    ┘
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
