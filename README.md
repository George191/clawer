# Spider

Spider 是一个面向站点模板化采集的 Python + FastAPI + React 项目。当前架构以 MinIO 发布的 YAML 模板及适配器为执行契约，通过 MongoDB、Kafka 和 PostgreSQL/TimescaleDB 完成采集与处理；仓库保留公共框架及已有来源的兼容插件。

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

### 浏览器采集适配器

需要 JavaScript 渲染或页面交互的采集适配器统一继承
`app.adapters.browser.BrowserSiteAdapter`。它复用现有 `SPIDER_AI_BROWSER_*`
浏览器配置和 Chromium 启动逻辑，一次采集保持一个隔离会话，单次请求结束关闭页面，
采集成功、异常或取消时统一释放浏览器。返回完整 HTML，不使用 AI 分析用的截断页面。

子类可设置 `browser_wait_selector`、`browser_wait_timeout_seconds`，并覆盖
`prepare_list_page(page)` / `prepare_detail_page(page)` 执行站点操作；钩子返回字符串时
将它作为解析输入，否则使用完整页面 HTML。`collect_table_rows(table)` 支持滚动表格，
保留虚拟列表移出 DOM 的行，未完成滚动时明确失败。详情请求使用
`request_detail_page()`，与列表共用会话。

基础版支持 GET、headers、cookies、params、直连和已配置的隧道代理；
`force_direct` 强制直连，不支持 HTTP 代理池的前置代理链。默认最多尝试三次，
配置及解析错误直接失败。Crawler 镜像包含 Playwright 和系统 Chromium，
部署这一基类需要重建 Crawler 镜像；仅更新 MinIO 站点源码不会安装运行依赖。

`templates/satsearch.yaml` 是独立的 HTTP 地面站来源，保留 Find-GS 数据署名、
空站名和完整多频段规格。

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

首次启用登录时，在服务器 `.env` 中设置 `FIRST_SUPERUSER`（邮箱）、
`FIRST_SUPERUSER_PASSWORD` 和 `FIRST_SUPERUSER_PHONE`。这些配置需要初始化后才会成为可登录账号。
构建 Web 镜像后执行一次：

```bash
docker compose build web
docker compose run --rm --no-deps web python -m app.web.initial_data
```

该命令先应用 Web 的 Alembic 迁移，创建 `public."user"`、`tenant`、`team` 等表，
再创建配置的管理员账号。已有账号不会被覆盖或重置密码，重复执行不会重复创建。
登录使用服务器 `.env` 中的邮箱和密码；初始化只写入密码哈希。

部署前应已准备好 AI Collect 数据库表结构。Web 和 Worker 日常启动只建立连接，不执行历史迁移或导入本地模板。来源模板和适配器通过 AI Collect 页面发布到 MinIO；一次性数据修复按实际需求在维护窗口执行，不放入标准运行代码。

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

Compose 为 news、patent、navwarn、intelligence、organization、financial_fact、filing、account、tweet
分别启动独立 Syncer 容器。原 `syncer` 服务负责 news，其余服务为 `syncer-<类型>`（服务名中的下划线改为连字符）。
每批从 MongoDB 读取 50 条，进程内最多 10 个协程同时发送及回写状态；Kafka 中每条记录仍是独立消息。
仅发送成功后标记对应版本为 synced，失败记录保留待重试。
采集记录落库即标记为 pending，Syncer 查询不依赖下载状态；每条记录下载处理完成后立即原子回写资源、下载状态、更新时间并重置为 pending，不等待同批其他记录。下载回写同时递增 download_version；同步确认匹配发送时的 updated_at 和下载版本，避免同一毫秒内的下载更新被旧同步确认覆盖。
同步由轮询触发：新采集记录和下载更新均在下一次轮询发送，空闲轮询默认间隔 10 秒，可通过 `--poll` 调整。
指定类型时，与下载器共用模板分组及 `ai_collect_template_alias` 别名映射查询，只读取匹配业务类型的实际集合；MongoDB 仅按原同步状态条件查询，不增加 `_meta.data_type` 过滤，发送前在内存中校验记录类型，不再用启动参数覆盖错误类型。

模板与集合的别名映射由 `app/records/collection_routes.py` 查询 `public.ai_collect_template_alias`，供采集、下载和同步共用。表结构由部署迁移维护，业务映射单独配置。映射以 `template_name` 关联已注册模板，`data_type` 表示该集合的业务类型，`collection_name` 表示实际 MongoDB 集合，`enabled` 控制按类型运行的下载和同步启停，不影响采集写入。模板存在映射时不再回退到同名集合，禁用映射也不会触发回退。
模板版本沿用 active 优先、更新时间倒序的选择规则；先关联路由再筛选业务类型，因此 SEC 父模板的类型无需改成 organization 或 filing。修改路由在下一批生效，已读入的当前批次继续完成。
模板的 `download[].collection` 使用实际集合名，变更实际集合时应一并更新对应下载配置。

模板标题和任务名称只使用来源或产品名，调度周期、执行方式和数据类型使用各自字段。同源多模板的内部 `name` 保留必要的栏目或端点区别；标识改名时须同步已发布 YAML、任务引用、集合路由和断点中的模板字段，保留任务及运行批次 UUID。执行入口使用任务当前的模板标识，避免旧队列参数覆盖改名后的引用。

NVD 采集模板 `nvd` / `nvd_feed` 通过别名分别写入 `nvd_vulnerability` / `nvd_feed_vulnerability`；两者的业务类型均为 `vulnerability`。模板标识与物理集合独立，模板改名通过显式路由保留既有集合、记录身份和下游来源。实际集合改名时需同时迁移记录的 `_meta.template`，保留记录 ID、业务内容、下载/同步状态及索引；已有下游数据还需核对 `data_source` 和原始记录元数据，不能仅重放造成来源重复。

例如禁止按类型运行的下载器与 Syncer 处理 SEC 财报集合：

```sql
UPDATE public.ai_collect_template_alias
SET enabled = false, updated_at = now()
WHERE template_name = 'sec_edgar' AND data_type = 'filing';
```

重新启用时将 `enabled` 设为 `true`。Syncer 接受任意合法类型名，RDS/ODS 不再为每个类型新增 handler。

```bash
python -m app.syncer.main --data-type financial_fact --batch 50 --concurrency 10
```

指定类型后发送到 `${SPIDER_KAFKA_TOPIC}-<类型>`，例如 `spider-crawler-financial_fact`。
不传 `--data-type` 时扫描全部类型，按每条记录的 `_meta.data_type` 自动发送到 `${SPIDER_KAFKA_TOPIC}-<类型>`，例如 organization 和 filing 分别发送到 `-organization` 和 `-filing`；缺少 `_meta.data_type` 的记录保留待处理，不发送到公共 topic。
同一范围只运行一个 Syncer 实例，勿同时运行全类型旧实例和新的分类型实例，以免重复发送。

新增来源可复用通用模式，不需要增加 Compose 的类型服务：

```bash
python -m app.syncer.main
```

该模式轮询已发布模板与集合路由；使用它时先停同范围的分类型 Syncer。现有 Compose 保留按已知类型分工的部署方式，不会自动为新类型增加进程。大批历史积压可能延后其他集合；需要单独处理新来源时可使用 `--template <模板或集合名>`。

切换前创建上述类型 topic（分区及副本数按集群配置），停止旧 Syncer，先更新 RDS Worker，再启动分类型 Syncer。
RDS 使用一个正则订阅 Consumer 接收旧 `SPIDER_ETL_RAW_TOPIC` 和 `${SPIDER_KAFKA_TOPIC}-<类型>`；ODS 正则订阅 `${SPIDER_ETL_RDS_TOPIC}-<类型>`。新 topic 无需修改类型列表，分区分配时恢复该分区的 offset，写入成功后再提交。消费组保持原配置；需要并行消费时按 Kafka 分区扩容同组实例。

SAM 两个 MongoDB 集合分别写入 `ts_rds.rds_contract`（`sam_gov_awards`）和
`ts_rds.rds_tender`（`sam_gov_opportunities`），标准化后对应 `ts_ods.ods_contract` 和
`ts_ods.ods_tender`。合同采集与同步使用 `contract`，采购公告使用独立的 `tender` 类型，按类型选择 RDS 表，保留 Mongo
记录身份及合同修订/交易号；ODS 金额使用 `NUMERIC`，复杂字段使用 `JSONB`，删除使用标志保留。
来源时间同时保留原文；未声明时区的时间不推定为 UTC。`created_at`/`updated_at` 为入库时间。
RDS/ODS 在首次处理数据时按统一字段定义自动注册、建表和创建分区，无需单独运行来源注册脚本。
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

合同之后新增的 `constellation`、`vulnerability`、`debrisevents`、`debris`、`launchvehicles`、
`frequency`、`groundstations`、`launchinfo`、`satellite`、`launchsites`、`orbital`、`organization`
均按同名类型进入 RDS 和 ODS。各表沿用 `(record_id, data_source, data_type)` 主键及 32 个哈希分区。
标准字段提取后，其余业务字段保存在 ODS 的 `attributes`；不确定日期保留原文，未声明单位的数值不换算。

公司与组织统一写入 `ts_rds.rds_organization` 和 `ts_ods.ods_organization`，ODS 保留 CIK、证券代码、交易所及原公司字段。
采集、Mongo、Syncer 和 RDS/ODS 的业务类型统一为 `organization`，使用 `syncer-organization`、`spider-crawler-organization`、`etl-rds-organization`。
SEC 组织的集合名和 `data_source` 为 `sec_edgar_company`；GCAT 组织集合和来源为 `organization_jonathan`。
SEC 的 companyfacts 接口由发布的采集适配器单独请求、展开观测值，直接写入 `sec_edgar_financial_fact`。组织采集与仓储不再接收或拆分 facts；事实身份与去重规则也随 SEC 适配器发布。`financial_fact` 单独经过 Syncer、RDS、ODS，修订标记等业务派生由其 normalizer 完成。
GCAT 模板技术标识 `jonathan_organizations` 保持不变。
表字段、主键、分区和索引集中声明在 `app/etl/schema/catalogs.py`，由 `baselines.py` 生成基线，
`registry.py` 管理 DDL 注册，ETL 启动和表恢复流程依据注册信息创建表与分区。
历史公司合并、组织改名和旧关系表清理属于一次性升级，不进入运行时流程。

新增来源的业务扩展与 YAML 一起通过现有 `adapter.py` 产物发布至 MinIO，不写入基础仓储或 `app.utils`：

- `NORMALIZERS = {"输入类型": normalize}`：函数接收原记录和 RDS 提供的顶层 `data_source`，返回一条或多条 ODS 记录；每张输出表的字段处理都在 normalizer 内完成。
- `ODS_FIELDS = {"新输出类型": {"字段": "TEXT"}}`：新表声明业务字段类型；身份、审计、主键、分区由通用执行器管理。现有表使用 `_CATALOG_FIELDS`，不要重复维护 SQL。字段仅允许基础 SQL 类型，不接受任意 DDL 片段。
- 特殊采集身份、合并或拆分逻辑可导出 `RECORD_ADAPTERS`，继承 `RecordAdapter`；普通来源直接使用通用 Mongo 仓储。

产物可以只有 ETL 导出，无需为此添加空的爬虫适配器类。运行时按业务 `data_source` 查询产物所有者（包括集合别名），每 60 秒刷新缓存；不从原始 `_meta.template` 重算业务来源。首次遇到新类型自动注册和建表；同一类型出现不一致字段声明时明确失败，不自动修改已有表或历史数据。
通用仓储只读取已发布产物的 `RECORD_ADAPTERS`，未声明时使用通用规则，不维护来源专用回退注册表。SEC、Twitter、SAM 等采集规则保留在各自 MinIO 产物中；本地不再保留 SEC/Twitter 数据处理辅助模块。Twitter 采集适配器独立保存账号并剥离推文内作者，ODS social normalizer 仅消费规范字段和稳定身份，不重复采集转换、查询 Mongo 或从推文再次生成账号记录。

部署时先暂停相关采集、下载、Syncer、旧 RDS 和旧 ODS，再启动新版进程。
为每个新增类型创建 `${SPIDER_KAFKA_TOPIC}-<类型>` 与 `${SPIDER_ETL_RDS_TOPIC}-<类型>` topic 后，
启动对应 Syncer，例如 `python -m app.syncer.main --data-type satellite`。
组织改名时的历史记录按原身份置为待同步，从 `organization` topic 重放。

目录表通过共同业务标识直接 JOIN，多值标识保留为本记录的 JSONB 数组，不维护关系表或待解析表。
目标尚未入库时查询自然无匹配；目标入库后，同一条 JOIN 即可查到，无须补边或改写来源记录。

| 类型 | 直接关联字段与目标 |
| --- | --- |
| constellation | `member_norad_ids` 包含 satellite 的 `norad_id` |
| vulnerability | 按 `cve_id` 对照不同漏洞来源；现有组织来源未提供可靠的共同组织标识 |
| debrisevents | `parent_gcat_id` 对 satellite 的 `gcat_id`；NORAD/COSPAR 对象号对卫星；`cospar_launch` 对发射批次；ESA 原生 `related_objects` 对 debris 的 `source_id` |
| debris | `norad_id`、`cospar_id` 对卫星和轨道；ESA `source_id` 对碎裂事件的对象列表 |
| launchvehicles | `manufacturer_codes` 包含 organization 的 `code`；型号 `name`、`variant` 对发射记录的 `rocket_name`、`vehicle_variant` |
| frequency | `norad_id`、`cospar_id`、`satnogs_sat_id` 对卫星；`itu_earth_notice_id` 对地面站 |
| groundstations | `itu_earth_notice_id` 对频率记录的地面通知标识 |
| launchinfo | `cospar_launch` 对卫星发射批次；`provider_codes` 包含组织代码；`site_code`、`pad_name` 对场地；型号和变体对火箭 |
| satellite | NORAD/COSPAR、SatNOGS ID、GCAT ID、发射批次；`owner_codes`、`manufacturer_codes` 对组织代码 |
| launchsites | `site_code`、`pad_name` 对发射记录；`operator_codes` 包含组织代码 |
| orbital | `norad_id`、`cospar_id` 对卫星 |
| organization | `code`、`parent_codes` 对 GCAT 组织及上下级；`cik` 对 SEC 财报与财务事实 |

新增 GCAT 关联代码只从该来源提取；多码拆分为数组，原展示字段保留。型号与变体关联须限定来源，不将显示名称作为跨来源的实体标识。
COSPAR 对象号与发射批次分列；ITU 仅将 WIC 的 `S` 类型通知写入地面通知字段，避免与卫星通知混用。
ESA 对象编号须同时限定来源和资源域；原生关系链接中没有目标 ID 时，不从 URL 的当前对象编号猜测目标。
NewSpace 等来源未给成员标识时保留原始信息；漏洞厂商与现有 SEC/GCAT 组织没有公共键时，不按名称强行关联。

```sql
SELECT o.record_id, s.record_id, s.name
FROM ts_ods.ods_orbital o
JOIN ts_ods.ods_satellite s ON s.norad_id = o.norad_id;

SELECT c.record_id, s.record_id, s.name
FROM ts_ods.ods_constellation c
CROSS JOIN LATERAL jsonb_array_elements_text(c.member_norad_ids) m(norad_id)
JOIN ts_ods.ods_satellite s ON s.norad_id = m.norad_id::bigint;

SELECT s.record_id, o.record_id, o.name
FROM ts_ods.ods_satellite s
JOIN ts_ods.ods_organization o ON s.owner_codes ? o.code;

SELECT f.record_id, g.record_id, g.name
FROM ts_ods.ods_frequency f
JOIN ts_ods.ods_groundstations g ON f.itu_earth_notice_id = g.itu_earth_notice_id;

SELECT e.record_id, d.record_id, d.name
FROM ts_ods.ods_debrisevents e
JOIN ts_ods.ods_debris d
  ON e.related_objects @> jsonb_build_array(
       jsonb_build_object('source_id', d.source_id, 'resource_type', 'object'))
WHERE e.data_source LIKE 'esa_discos%' AND d.data_source LIKE 'esa_discos%';
```

Mongo 使用 `_meta.template` 保存实际集合标识，采集适配器不再额外注入 `_meta.data_source`；通用保存流程不清理历史元数据字段。
RDS 是采集侧到业务侧的入口，将 `_meta.template` 映射为业务记录的 `data_source`；缺少 template 时拒绝写入。
ODS 及后续 ETL 只使用业务记录的 `record.data_source` 选择处理器和写入来源，不再读取采集侧 `_meta.template` 或旧 `_meta.data_source` 推导来源，也不接受 normalizer 覆盖。
财务事实的业务类型为 `financial_fact`，Mongo 集合与 `_meta.template` 均为 `sec_edgar_financial_fact`，新版 RDS 将它映射为同名业务来源。
Twitter 的 `tw_account`、`tw_tweet` 也按各自 template 作为来源，业务平台标识及 ID 算法仍使用 `twitter`。
历史来源是 PostgreSQL 复合主键和分区键的一部分，需要单独迁移；迁移前不能按新来源全量重放，避免形成两套来源身份。

```sql
SELECT f.record_id, f.concept, f.value, o.name
FROM ts_ods.ods_financial_fact f
JOIN ts_ods.ods_organization o
  ON lpad(ltrim(btrim(f.cik), '0'), 10, '0') = lpad(ltrim(btrim(o.cik), '0'), 10, '0')
WHERE btrim(f.cik) ~ '^[0-9]+$' AND length(ltrim(btrim(f.cik), '0')) BETWEEN 1 AND 10
  AND btrim(o.cik) ~ '^[0-9]+$' AND length(ltrim(btrim(o.cik), '0')) BETWEEN 1 AND 10;
```

各 topic 内仍按顺序逐条处理、成功后提交，数据库连接池由 RDS 统一管理。
RDS 按实际目标表输出到 `${SPIDER_ETL_RDS_TOPIC}-<表名后缀>`，例如 `rds_news` 对应 `etl-rds-news`、`rds_financial_fact` 对应 `etl-rds-financial_fact`（以配置的 topic 前缀为准）。
上线前创建这些 RDS 输出 topic，并先更新 ODS 再更新 RDS；ODS 订阅带类型后缀的 RDS topic，逐条处理并输出到配置的公共 ODS topic。
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

1. YAML 产物描述站点 URL、请求、字段映射、分页、下载资源、批量参数等。
2. `adapter.py` 产物处理站点特有行为，并按需导出记录规则和 ODS normalizer/字段声明。

新增来源通过 AI 生成、确认后发布到 MinIO；不要为新增来源修改基础执行代码。仓库内的 `templates/` 和既有适配器模块用于兼容。以下是发布产物示例：

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

对应的 MinIO 适配器产物示例（新业务类型还需按上文导出 `NORMALIZERS` 和 `ODS_FIELDS`）：

```python
from __future__ import annotations

from app.adapters import BaseSiteAdapter, register_adapter


@register_adapter("my_site")
class MySiteAdapter(BaseSiteAdapter):
    adapter_name = "my_site"

    def on_request_headers(self, page: int) -> dict[str, str]:
        return {"Referer": self._base_url}
```

已发布执行从 MinIO 加载适配器，模板中的 `adapter: my_site` 绑定产物内对应注册名。只有模板未声明爬虫适配器时才使用通用采集逻辑；声明了但无法加载时明确报错。已有本地模块仅保留兼容加载。

## 配置

采集适配器直接输出规范字段：按明确映射移除已消费的原始别名，保留未映射的独有字段。对象和数组保持业务结构；存储、Kafka、RDS 与采购 ODS 直接读取同一套顶层字段，不再转换历史容器或保留原始字段映射表。

显式映射也覆盖原始嵌套叶子，只删除已消费的字段及其空父对象，保留未知子字段。未映射的同名值发生冲突时停止写入，不能静默覆盖。入口及最终合并结果中，任何业务层级出现退役的 `source_data` 容器都会被拒绝；历史转换只在独立维护工具中执行。

所有层级的业务字段名统一使用小写 `snake_case`，包括数组内对象。系统字段及其运行上下文保持原样；动态字典中的业务标识（例如 SEC 的 taxonomy、concept 和 unit）作为数据保留。两个不同字段归一后重名时必须明确解决，禁止静默覆盖。

采购来源时间统一使用 `source_created_at`、`source_updated_at`；发布时间与业务日期保留各自含义。SAM 合同保留完整的 `principal_naics`，主代码由 ODS 派生；合同金额以 `action_obligation` 为准，ODS 的 `amount` 沿用该值。合同发行机构与签约机构是独立角色，不能合并身份字段。

部署结构变更时需要同时更新采集、同步和 ETL 进程及已发布的模板、适配器。在维护窗口内等待旧采集批次结束，完成代码、模板发布和历史结构修正后再恢复消费，避免结构差异被当成业务变化。历史记录维护保留记录身份、时间、下载和同步状态。合同、招标业务内容未变化时，RDS 不重复更新，也不再次向 ODS 发送记录；分类标记修正不视为业务更新。

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
