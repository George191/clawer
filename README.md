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
| Downloader | `python -m app.downloader.main` | 消费 Mongo CDC 事件，下载资源到 MinIO/本地，MongoDB 低频补偿 |
| Syncer | `python -m app.syncer.main` | 消费 Mongo CDC 事件，按当前 Mongo 记录发送 Kafka，MongoDB 低频补偿 |
| ETL | `python -m app.etl.main --layer <layer>` | 消费 Kafka，写入 Postgres/TimescaleDB |
| Web API | `uvicorn app.web.main:app --host 0.0.0.0 --port 8000 --reload` | FastAPI 管理后端 |
| Web Panel | `npm run dev` in `web-panel/` | React 管理界面 |

下载器按 PostgreSQL 模板分组并关联 `public.ai_collect_template_alias`，以映射的业务 `data_type` 筛选实际集合；未配置映射的已注册模板按 `数据类型_网站或端点标识` 生成集合名。通过独立消费组接收 Debezium Mongo CDC 事件，读取 MongoDB 当前待下载记录并原子认领；采集器不再主动投递 Redis 下载快照。MongoDB 默认每 60 秒扫描补偿待下载记录，集合路由在补偿间隔内缓存。不传 `--datatype` 时处理所有已启用路由，也可与 `--template` 同用；`--template` 支持父模板名和实际集合名：

```bash
python -m app.downloader.main --datatype news
python -m app.downloader.main --template ssc_news --datatype=news
python -m app.downloader.main --template sec_edgar --datatype filing
```

下载并发使用 `SPIDER_DOWNLOAD_ASSET_CONCURRENCY`（默认 64），实际同时处理记录数不超过 `--batch`；完成一条即补充下一条，不等待整批中最慢的下载，每条记录内的资源仍依次下载。仅领取 `pending` 记录，认领须匹配 MongoDB 当前版本。活动下载每 60 秒续租，超过 5 分钟无心跳的 `downloading` 在低频补偿时恢复为 `pending`；旧认领令牌不能回写结果。正常取消会释放认领，记录结束时释放对应的 HTTP 会话和代理租约。升级时重启所有下载器，避免无心跳的旧版进程与新版回收同时运行。

## 外部基础设施

MongoDB、Redis、MinIO、Kafka 和 PostgreSQL/TimescaleDB 由外部环境提供，
`docker-compose.yml` 默认只启动项目应用服务；显式启用 `mongo-cdc` profile 时增加 Debezium Kafka Connect。
连接地址、认证信息和业务 Topic 从 `.env` 读取；CDC Topic 和消费组按业务 Topic、数据库及服务处理范围派生。

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
认证、用户、租户表和 Flow 数据分层统一使用 `SPIDER_PG_URL` 指定的数据库，
连接地址必须在 Web 容器内可达。容器中的 `localhost` 指容器自身，不能沿用开发机的本地转发地址。
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
每批最多处理 50 条，进程内最多 10 个协程同时发送及回写状态；Kafka 中每条记录仍是独立消息。
仅发送成功后标记对应版本为 synced，失败记录保留待重试。
采集记录落库即标记为 pending，Syncer 查询不依赖下载状态；每条记录下载处理完成后立即原子回写资源、下载状态、更新时间并重置为 pending，不等待同批其他记录。业务写入生成唯一 write_token，下载回写同时递增 download_version；同步确认匹配发送时的 write_token、updated_at 和下载版本，避免同一毫秒内的新业务内容或下载更新被旧同步确认覆盖。历史记录没有 write_token 时仍可同步；旧快照不会确认后来生成新 token 的版本。下载认领也检查相同业务写入标记。
采集器只写 MongoDB，不再发送 Kafka 或投递 Redis 下载快照。Debezium 将 Mongo Change Streams 写入 `${SPIDER_KAFKA_TOPIC}-mongo-cdc.<数据库>.<集合>`；下载器和 Syncer 各自使用独立消费组，按已启用的集合路由消费事件，再读取 MongoDB 当前版本。Syncer 复用现有完整记录格式、`record_id` 分区键和业务类型 topic；仅在独立 Syncer 内等待 Kafka ACK，失败记录保持 `pending`。发送时去除 Mongo `_id`，元数据时间与 Mongo 的毫秒精度一致。
CDC 用于驱动当前状态同步，不是逐条保留历史版本的审计流；连续更新可以合并为最新状态。写入契约仍是业务变更更新 `_meta.updated_at` 并设置 `_meta.sync_status=pending`，下载完成还需递增 `_meta.download_version`；需要下载的记录须保持 `_meta.download_status=pending`。同步 ACK、下载认领及心跳等纯内部状态事件被过滤，下载结果更新会触发同步。原样重复采集不触发业务发送；物理删除暂不向下游传播，现有业务软删除仍按普通记录更新处理。禁用来源仍由集合路由过滤，不因数据库级 CDC 而开放处理。
MongoDB `pending` 状态保留为补偿依据：Syncer 默认每 60 秒扫描一次（`--poll`），同步和下载补偿均跳过最近一个补偿间隔内更新的记录。满批积压可连续处理，不必每批等待整个补偿间隔。CDC 通知读取后推进 Kafka 消费位点，后续处理失败由持久化的 `pending`/下载认领状态恢复；Kafka 不可用、CDC 中断或进程退出时仍保留扫描补偿。允许重复消息，沿用版本 ACK 和下游版本检查。Redis 不再承担这条下载或同步数据投递链路。
指定类型时，与下载器共用模板分组及 `ai_collect_template_alias` 别名映射查询，只读取匹配业务类型的实际集合；MongoDB 按同步状态和补偿等待时间查询，不增加 `_meta.data_type` 过滤，发送前在内存中校验记录类型，不再用启动参数覆盖错误类型。

### Debezium 整体迁移与发布门槛

MongoDB 与 PostgreSQL 的出站统一由 Debezium Connect 捕获，Python Worker 保留采集、下载、标准化及算法职责。Mongo 必须为支持 Change Streams 的副本集或分片集群；账号需要目标数据库的读取及 `changeStream` 权限，Connect 必须能访问拓扑公布的全部节点。Postgres 使用 `pgoutput`，本实现要求 PostgreSQL 15+、`wal_level=logical`、发布表完整行镜像和持久复制槽。部署使用 Debezium Connect 3.4，参见 [MongoDB 文档](https://debezium.io/documentation/reference/3.4/connectors/mongodb.html)及 [PostgreSQL 文档](https://debezium.io/documentation/reference/3.4/connectors/postgresql.html)。

资源下载按模板 `download` 与适配器生成的下载项比较输入签名，普通标题等业务字段更新不重下文件；URL、资源版本、目标资源字段等输入变化时失效对应旧路径并重新排队。纯输入计划随记录冻结到 `_meta.download_plan`，同名模板发布新版本不会改写旧任务；缓存按 `SPIDER_DOWNLOAD_TEMPLATE_CACHE_SECONDS` 刷新，摘要不匹配时立即验证。模板可声明 `version_selector`，适配器可提供 `DownloadItem.version`；同 URL 内容变化而来源未提供版本信息时，系统无法自动判断。历史无签名的文件会安全重验一次；无冻结计划且无法验证历史摘要的记录会明确失败并要求重新采集，不会无限挂起或假报完成。下载认领、资源输入和对象路径均隔离旧版本，业务 ACK 仍使用 `write_token` 与下载版本检查。并发资源更新使用 CAS，仅冲突记录重读，资源变化路径会增加单条数据库请求，不能把离线微基准当成实际数据库吞吐。

PG 各实际已实现层的 current 表写入经 WAL → Debezium → 原始 CDC topic → `postgres-syncer` → 原业务 topic。relay 在同一 Kafka 事务中发送输出与提交源位点，下游消费者使用 `read_committed`；这不等于数据库、算法调用及所有外部系统之间具备端到端 exactly-once。RDS/ODS/TASK/DWD 已存在的业务逻辑保留；DWS/DIM 没有业务 handler、ADS 没有 worker 的部分不被宣称为已实现功能。PG 适配使用事件本身的完整 `after`，不回读最新数据库冒充历史快照；JSONB、金额、微秒时间和空间字段按 Connect schema 解析。缺失 TOAST 数据或无契约的物理删除会阻止位点推进并报告错误，业务软删除仍正常传播。

工作台在 CDC 模式下拒绝 current 表的物理行删除，防止一个受支持的 UI 操作把 relay 卡在无删除契约的事件上；应通过业务软删除表达失效。普通历史/自定义 schema 继续使用既有主键及权限校验。直接在数据库执行破坏性操作仍需遵守迁移/恢复流程。

先用现有环境配置进行只读检查，不修改 Mongo 拓扑、不注册 connector：

```bash
python -m app.syncer.mongo_cdc --check
```

Connect 复用 `SPIDER_KAFKA_BROKERS`、`SPIDER_KAFKA_TOPIC` 及已有 Kafka SASL/安全协议配置。内部配置、位点和状态 topic 分别为 `${SPIDER_KAFKA_TOPIC}-mongo-connect-configs`、`-offsets`、`-status`，必须保留；其副本数由 `SPIDER_MONGO_CDC_REPLICATION_FACTOR` 控制，默认 3，单 broker 测试环境需显式设为 1。CDC topic 为单分区，副本数使用 broker 默认值，消息上限设为 16 MiB。PG relay 同时要求 broker 正确配置事务状态日志、副本及 ACL；账号需要原始/业务 topic 的读写、创建、消费组与 transactional ID 权限。Python 与 Connect 共享 `SPIDER_KAFKA_SSL_CAFILE`，留空使用系统信任库；自签 CA 可配置为容器内 `/etc/kafka/kafka.crt`，证书须与 broker 主机名匹配，不再关闭证书校验。旧 `SPIDER_MONGO_CDC_TLS_CA` 仍兼容 Connect。REST 接口含连接配置，仅映射本机 `127.0.0.1:8083`；管理页面通过 `SPIDER_CDC_CONNECT_URL` 指向可访问的受保护管理地址。

先启动 Connect、构建新版 Syncer 镜像，再注册 connector；注册命令仅在 connector 和任务都为 `RUNNING` 后成功退出，不输出连接密码或服务端错误正文：

```bash
docker compose up -d mongo-cdc-connect
docker compose build syncer
docker compose run --rm --no-deps syncer python -m app.syncer.mongo_cdc --connect-url http://mongo-cdc-connect:8083
```

注册采用 `snapshot.mode=no_data`，不全量重发已同步记录，历史待下载/待同步记录由现有扫描补偿处理。确认 connector 正常后，通过现有发布流程重建并重启 crawler、全部下载器及所有正在运行的 `syncer`/`syncer-*` 实例；不要让同一范围的新旧下载器、同步器长期并行运行。验证新增记录、业务更新、下载回写均能到达原业务 topic，并检查同步 ACK/下载心跳没有产生业务消息循环。暂停并恢复 Connect 后应从内部 offset topic 续接；停机超过 oplog 保留窗口时需检查 connector 状态并恢复采集，期间 Mongo 补偿继续处理当前待处理状态。不要删除内部 topic 来重置正常位点。

PG 首次切换须先暂停 ETL 写入并排空旧发送路径，按以下顺序准备。`--prepare` 会修改 publication、REPLICA IDENTITY 与 lineage 列，须在部署环境审阅执行；`--check` 只读，不能与 `--prepare` 同用。准备阶段使用 `SPIDER_PG_URL` 指定的部署 owner/DBA 身份，须具备表迁移及 schema publication 创建权限；准备完成后恢复普通应用凭据，不要给长期复制账号追加 DDL 管理权限。检查、注册由 `SPIDER_PG_CDC_URL` 指定的独立复制账号执行，ETL 应用账号无需持有复制权限。连接串使用完整 SQLAlchemy asyncpg URL，目标数据库须与应用相同。

```bash
docker compose build syncer rds-worker ods-worker task-worker
docker compose run --rm --no-deps rds-worker python -m app.etl.main --init-schema
docker compose run --rm --no-deps syncer python -m app.syncer.postgres_cdc --prepare
docker compose run --rm --no-deps syncer python -m app.syncer.postgres_cdc --check
docker compose run --rm --no-deps syncer python -m app.syncer.postgres_cdc --connect-url http://mongo-cdc-connect:8083
docker compose up -d postgres-syncer
```

确认 PG connector/task 为 RUNNING 且 slot 位点有效后再启动 ETL。`SPIDER_ETL_OUTPUT_MODE=debezium` 为默认模式；未具备 publication/slot 时 ETL 会拒绝消费输入，避免先写库而没有变更捕获。默认 `no_data` 只接续注册后的变化，首次注册前的历史数据必须单独回填，不能依赖不存在的旧 slot 补回；若需初始快照可显式选择 `--snapshot-mode initial`，先估算数据库和 Kafka 负载。原始 PG topic 为 `${SPIDER_KAFKA_TOPIC}-postgres-cdc.<schema>.<table>`，不要删除内部位点 topic 或直接重置复制槽。复制槽保留 WAL，须对保留字节、未确认字节及磁盘空间设置部署环境告警。

TASK 的 `source_revision` 与 DWD 的 `ods_revision`/`task_revision` 保存完整上游 ODS 输入的规范摘要。算法在锁外执行，落库前验证当前源行；ODS 换版清除不匹配的旧算法结果，图谱仅发布 revision 一致的完整记录。算法没有声明输入字段契约，因此摘要保留所有持久输入字段，包括审计时间，可能增加重算次数，需要压测。历史 NULL lineage 代表关联未知，不能据此发布图谱；须从最新 ODS 显式重算 TASK/DWD，`no_data` 不会代替这项迁移。RDS 同业务版本只接受同 topic/partition 的 offset 有序更新；跨流同版本但内容冲突会阻断，切换 topic 时必须先排空并核对版本，不能通过静默跳过规避。

`direct` 只作为受控回滚模式：先停止 relay、排空或隔离在途输出，再将 ETL 切为 `SPIDER_ETL_OUTPUT_MODE=direct` 并重启，禁止两条出站路径并行。保留 Connect 内部 topic/复制槽以便恢复，并核对下游版本与身份。ES 的索引版本不能混用无 LSN 的旧消息与 CDC 版本；需要重新建立隔离索引、核对后切换 alias，不能假定 direct 模式自动回滚全部 ES 状态。

ES 默认文档身份使用业务三元组，迁移旧 `record_id` 索引时须创建新索引与新的 `syncer_name`。先创建全部配置的 RDS 业务 topic，启动 CDC 消费并等待完成分区分配，再执行 `--once` 历史回填；新消费组从启动时的最新位点接续，已有组恢复已提交位点，缺少 topic 会拒绝启动。核对数量/样本后原子切换 alias，旧索引保留供回滚，不自动删除。历史回填按时间加业务身份的复合游标和固定上界推进；无 CDC LSN 的历史写入只能补不存在的文档，不能覆盖已接收到的新版本。连续 direct 模式和 CDC 消息缺失 LSN 时 ES 拒绝处理，避免把冲突当成同步成功。

### 实施分工与验收

| 角色 | 负责范围 | 验收重点 |
| --- | --- | --- |
| 项目负责人/PM | 接口协调、范围、发布与回滚门槛 | 不以离线测试代替生产验收 |
| 架构/大数据 | WAL、publication、分区、relay、ES | 位点恢复、事务、类型精度、历史回填 |
| Python | 资源身份、Mongo 并发、下载认领 | 普通字段不重下、旧结果不覆盖新资源 |
| 产品/前端/UI | CDC 管理状态、监控与错误提示 | 真实读数，未采集显示未知，不伪造健康 |
| 独立质检 | 跨模块复核、故障回归与发布核对 | 先复现再修复，阻断项关闭后复验 |

仓库默认 `python -m pytest` 收集 `app` 下的回归测试；同名测试模块采用 importlib 模式。前端运行 `npm run build` 和已有 Node 回归。离线 CPU 基准可运行 `python -m app.quality.benchmark_cdc --records 10000 --rounds 5 --payload-bytes 1024`，报告明确不包含网络、数据库或 Kafka，不能作为端到端容量承诺。

单元测试通过 `app/conftest.py` 使用惰性、本机不可用连接，避免依赖开发者的真实 `.env`；运行时镜像不包含测试目录或 conftest。可选真实 PG 回放测试须显式设置 `SPIDER_TEST_PG_URL`，并限制为 loopback 主机及明确 test 库名，仅创建临时表。2026-11-02 本轮最终离线回归为 433 通过、1 个真实 PG 用例因缺少测试库跳过；Node 回归 12 项通过，TypeScript/Vite 构建通过。本地模拟 API 的 Edge 检查覆盖 CDC 未配置与任务失败状态、桌面与 390px 宽度，不等于真实环境联调。

下载 CLI 保持 `--datatype`，并接受同义的 `--data-type`；未知、缺值或误拼的范围参数会直接报错。旧文档中的 `--cik-file` 从未实现筛选，现在明确拒绝该参数，避免误以为已限制范围而下载全部集合。

全仓静态审计另发现既有孤立文件 `app/web/core/graph/build.py` 依赖缺失的 members/workflow 模块，且引用未导入的 `AsyncPostgresSaver`。该文件不在当前正式路由调用链，本轮未擅自删除或以新功能替换；它不计入已验收功能，全仓静态检查仍保留此项遗留错误。当前实际链路中的假监控指标、重复依赖导入、校验器返回未过滤数据、连接信息日志与数据库异常响应泄露等问题已分别修正并补回归。

投产前仍须在测试/预生产环境验证：真实 TLS/ACL、PG15+ publication/新分区、RI FULL/大 JSONB、Kafka 事务中止与重启、Mongo 断线与 oplog 窗口、旧下载结果/并发采集、ES 回填与 alias 回滚；并测端到端 P50/P95/P99、稳定/峰值吞吐、数据库读写/WAL 与 Kafka lag。当前尚无用户确认的容量或延迟目标，也无联调环境，因此真实环境性能与生产验收不能标为通过。

模板与集合的别名映射由 `app/records/collection_routes.py` 查询 `public.ai_collect_template_alias`，供采集、下载和同步共用。表结构由部署迁移维护，业务映射单独配置。映射以 `template_name` 关联已注册模板，`data_type` 表示该集合的业务类型，`collection_name` 表示实际 MongoDB 集合，`enabled` 控制按类型运行的下载和同步启停，不影响采集写入。模板存在映射时不再回退到同名集合，禁用映射也不会触发回退。
模板版本沿用 active 优先、更新时间倒序的选择规则；先关联路由再筛选业务类型，因此 SEC 父模板的类型无需改成 organization 或 filing。修改路由在服务刷新路由缓存后生效，已开始的记录继续完成。
模板的 `download[].collection` 使用实际集合名，变更实际集合时应一并更新对应下载配置。

记录的 `_meta.template` 与实际集合名统一，ETL 直接注册标准来源名，不保留旧来源别名或额外的 `_meta.collection`。上线前须一次性完成发布模板、适配器、集合路由、Mongo 历史元数据及 RDS/ODS 来源与主键的迁移，并处理在途消息、重启相关服务；本仓库不提供保留旧身份的集合重命名脚本。

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

卫星漏洞抽取（`satellite_vulnerability_extract`）沿用 PDF 内容提取的任务注册与结果落表方式：

- `app/etl/tasks/extractors/` 与 `converters/` 并列，包含抽取接口 `BaseExtractor`、通用关键词匹配器 `KeywordExtractor` 和词表版本/缓存管理 `KeywordFileStore`。专项任务仅选择业务字段并封装结果；新增关键词专项任务可复用匹配器，并以任务名建立隔离的词表目录。
- 关键词保存在现有 MinIO bucket 的 `tasks/satellite_vulnerability_extract/keywords/versions/<UUID>.yaml`，`tasks/satellite_vulnerability_extract/keywords/current.json` 指向当前版本；每次上传保留独立文件，启用历史版本即可回滚。并发启用以最后成功写入指针的操作为准，不修改 bucket 的版本设置，也不使用数据库配置表。
- 在 Flow → `ts_task` → `task_vulnerability` → **Satellite extraction** 中查看当前词表、下载 YAML 和历史文件；管理员可上传并启用新文件或启用历史版本。文件格式为 `keywords: [GPS, VSAT]`，支持空数组停用；限制为 UTF-8 YAML、1 MiB、1000 个词、每词 200 字符。
- 前端直接下载 MinIO 中当前启用的词表，不读取本地示例文件。TASK 不自动上传或回退到默认词表；未配置、文件损坏或 MinIO 读取失败均报错。任务缓存当前词表最多 30 秒，版本不变时不重复下载词表文件。
- 匹配 ODS 的 `description`、`descriptions`、`affected`、`weaknesses`、`configurations` 中的字符串值，忽略大小写并使用单词边界；不匹配字段名、引用链接或系统元数据。原 CVE 规则之外，补充支持 NVD 的受影响产品配置。
- 仅命中记录写入 `ts_task.task_vulnerability.result`（JSONB 数组，例如 `["satellite", "satellite modem", "GPS"]`），`result_type=json`；同一来源 record/task 更新最新命中结果，无命中视为处理成功并提交消费进度，不写库、不发送下游消息，不复制 ODS 内容。

Compose 中的 `task-worker` 使用 `python -m app.etl.main --layer task --table vulnerability`，仅处理漏洞并在启动时自动建结果表。部署时重建 Web API 和 TASK worker，并在前端上传首个词表。已有记录不会因配置变化自动重算；需要重跑对应 ODS 消息。文件接口位于 `/etl/task-config/vulnerability-keywords`（API 前缀按部署配置）：`GET` 查询当前词表和版本，`POST /versions` 上传文件（multipart `file`），`PUT /current` 启用版本（`{"version": "UUID"}`），`GET /versions/{version}/file` 下载历史文件，`GET /current/file` 下载当前 MinIO 文件（未启用词表时返回 404），原 `GET /example` 路径兼容返回当前文件。

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

### Google 搜索采集

智能采集首页切换到「Google 搜索」，输入关键词、页数（默认 3 页，最多 100 页），
选择是否保存详情页原文后开始采集。该入口使用确定性的模板和适配器，不执行 AI 模板生成。
首次创建任务时，将 `app/web/collection_sources/google_search/` 下的 `template.yaml`
及 `adapter.py` 发布为 `google_search@v1.1`；已有版本不自动覆盖。`v1.1` 修复服务器上
`curl_cffi` 的 `CONNECT_TO` 原生参数兼容问题，旧任务仍保留原版本。

`POST /api/v1/ai/google-search/tasks` 接受 `query`、`language`（默认 `zh-CN`）、
`max_pages` 和 `fetch_details`（默认 `true`），返回普通采集任务。
创建后通过已有任务操作接口启动，前端自动完成这一步；启动失败可在任务面板重试。

创建任务时注册禁用下游的集合路由，复用现有「允许 MongoDB 写入、禁用下游消费」机制，
不自动改动已存在的冲突路由。MongoDB 集合为 `search_result_google_search`，包含 `kind=list_page` 的列表页记录和
`kind=result` 的搜索结果记录，按查询、语言、记录种类和 URL 去重。
结果提取标题、链接、摘要等字段，完整列表页保留未结构化的其他区域信息；
列表页保存完整渲染 DOM，详情页通过静态 HTTP 保存服务器返回的原始 HTML 字节，不执行详情站点脚本。
两类页面原文均使用现有资源存储保存到 MinIO，MongoDB 保存原文路径和抓取状态。
详情失败保留搜索结果及错误信息；Google 验证码、同意页或无法识别的页面明确报错。
页数是采集上限，结果结束时提前停止。

部署需更新 Web、前端及 Crawler（使用本来源禁用 Service Workers 的浏览器上下文选项）。
Web 和 Crawler 均需配置现有 MongoDB、MinIO 及 Playwright/Chromium 运行环境。
本入口完成搜索采集入 MongoDB，不新增 `search_result` 的同步或 ETL 服务。

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
