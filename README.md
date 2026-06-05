<!-- python -m app.main google_patent:assignee=Lockheed+Martin+Space
python -m app.main downloader             # Downloader only
python -m app.main syncer                 # Syncer only
python -m app.etl.main --init-schema      # 初始化表结构
python -m app.etl.main --layer rds        # 单层启动
python -m app.etl.main --layer all        # 全部三层串联- -->

# 🕷️ AI Collector — AI 驱动的通用采集平台

> 从 Google Patents 采集到 Kafka → ETL 七层管道 → 知识图谱 → AI 分析，一站式大数据采集 & 处理平台。

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![React](https://img.shields.io/badge/react-18-61DAFB.svg)](https://react.dev/)
[![FastAPI](https://img.shields.io/badge/fastapi-0.111-009688.svg)](https://fastapi.tiangolo.com/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

---

## 🏗️ 架构总览

```
┌──────────────────────────────────────────────────────────────────┐
│                        🌐 采集层 (Crawl)                          │
│  Chrome CDP / HTTP Client  →  Google Patents  →  MongoDB (Raw)   │
└───────────────────────────┬──────────────────────────────────────┘
                            │ Kafka Pipeline
┌───────────────────────────┼──────────────────────────────────────┐
│                    ⚙️ ETL 七层管道                                │
│                                                                   │
│  RAW ──→ RDS ──→ ODS ──→ TASK ──→ DWD ──→ DWS ──→ ADS          │
│  原始    入库    标准化   算法分析   合并宽表   汇总     应用       │
│                                 │                                  │
│                          DIM ◄──┘  (维度表)                       │
│                            │                                      │
│                     GRAPH ◄┘  (知识图谱)                          │
└───────────────────────────┬──────────────────────────────────────┘
                            │
┌───────────────────────────┴──────────────────────────────────────┐
│                     🖥️ Web Panel (React)                          │
│  Dashboard │ 数据探索 │ 任务中心 │ 采集监控 │ 管道管理 │ 模板管理   │
└──────────────────────────────────────────────────────────────────┘
```

### 基础设施

| 组件 | 用途 | 端口 |
|------|------|------|
| **MongoDB** | 原始采集数据存储 | 27017 |
| **PostgreSQL + TimescaleDB** | ETL 管道 & 时序数据 | 5432 |
| **Kafka** | 消息队列 (7 层管道) | 9092 |
| **Redis** | 去重 & 偏移量持久化 | 6379 |
| **MinIO** | 文件存储 (PDF/图片) | 9000/9001 |

---

## 🚀 快速开始

### 前提条件

- **Docker** & **Docker Compose** (推荐) 或手动安装基础设施
- **Python 3.10+** (运行采集 & ETL 后端)
- **Node.js 18+** (运行 Web 面板前端)

### 方式一：Docker 一键启动 (推荐)

```bash
# 1. 克隆项目
cd /path/to/ai-collector

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env，按需修改密码和端口

# 3. 启动全部基础设施 + ETL 管道
make docker-up

# 4. 运行采集任务（示例：采集 Google Patents）
make run crawl google_patent

# 5. 启动 Web 面板前端
cd web-panel && npm install && npm run dev
```

浏览器打开：
- **Web 面板**: http://localhost:5173
- **API 文档**: http://localhost:8000/docs
- **MinIO Console**: http://localhost:9001 (minioadmin/minioadmin)

### 方式二：本地开发启动

```bash
# ── 1. Python 后端 ──
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env 确保基础设施地址正确
python -m app.main

# ── 2. ETL Worker（可选，按需启动各层）──
make etl-rds     # RDS 层：原始数据入库
make etl-ods     # ODS 层：数据标准化
make etl-task    # TASK 层：AI 算法分析
make etl-dwd     # DWD 层：合并宽表 → 推送图谱
make etl-all     # 启动全部 ETL Worker

# ── 3. Web Panel ──
make web         # 启动 FastAPI Web 服务 (端口 8000)

# ── 4. React 前端 ──
cd web-panel
npm install
npm run dev      # 启动 Vite 开发服务器 (端口 5173)
```

---

## 📂 项目结构

```
app/
├── app/                          # Python 后端核心
│   ├── main.py                   # 入口 (采集引擎)
│   ├── config/settings.py        # 全局配置 (Pydantic Settings)
│   ├── engine/                   # 采集引擎 & 模板加载
│   │   ├── spider_engine.py      # 采集引擎核心 (支持适配器)
│   │   ├── template_loader.py    # YAML 模板加载器
│   │   ├── jinja2_renderer.py    # Jinja2 动态模板渲染
│   │   └── browser_events.py     # 浏览器事件封装 (gen_204)
│   ├── models/template.py        # 模板数据模型
│   ├── parser/template_parser.py # 页面解析器
│   ├── downloader/http_client.py # HTTP 客户端 (httpx + curl_cffi)
│   ├── adapters/                 # 站点适配器 (可插拔)
│   │   ├── __init__.py           # BaseSiteAdapter + 注册表
│   │   └── google_patent.py      # Google Patents 适配器
│   ├── anti_crawl/               # 反爬模块
│   │   ├── identity_rotator.py   # UA/IP 轮换
│   │   ├── proxy_pool.py         # 代理池
│   │   └── request_delayer.py    # 请求延迟
│   ├── dedup/                    # 去重模块 (Redis/Bloom)
│   ├── etl/                      # ETL 七层管道
│   │   ├── base.py               # ETLBase 基类 (Topic-Aware 路由)
│   │   ├── ts_rds.py             # RDS — 原始数据入库
│   │   ├── ts_ods.py             # ODS — 数据标准化
│   │   ├── ts_task.py            # TASK — AI 算法分析
│   │   ├── ts_dwd.py             # DWD — 双源合并宽表 + 完整度门控
│   │   ├── ts_dws.py             # DWS — 汇总层
│   │   ├── ts_dim.py             # DIM — 维度表
│   │   ├── normalizers/          # 标准化器注册中心
│   │   └── offset_manager.py     # Kafka Offset 管理
│   ├── ai/                       # AI 智能模块
│   │   ├── sql_hints.py          # SQL 智能提示 (592行)
│   │   └── dashboard_metrics.py  # 实时监控指标 (509行)
│   ├── storage/                  # 存储层
│   │   ├── mongo_storage.py      # MongoDB
│   │   ├── minio_client.py       # MinIO
│   │   ├── kafka_producer.py     # Kafka Producer
│   │   └── file_storage.py       # 本地文件
│   ├── web/                      # FastAPI Web 面板
│   │   ├── main.py               # Web 入口 + Dashboard
│   │   └── routes/               # API 路由
│   │       ├── tasks.py          # 任务管理
│   │       ├── templates.py      # 模板 CRUD
│   │       ├── data.py           # 数据查询/导出
│   │       ├── kafka_admin.py    # Kafka 管理 (6 端点)
│   │       ├── redis_admin.py    # Redis 管理 (5 端点)
│   │       ├── etl_admin.py      # ETL 管理 (9 端点)
│   │       └── scheduler_admin.py # 调度管理 (3 端点)
│   └── scheduler/                # 任务调度
│       ├── priority_queue.py     # 优先级队列
│       └── rate_limiter.py       # 速率限制
├── tests/                        # 测试 (pytest + async)
├── templates/                    # 采集模板 (YAML)
│   └── google_patent.yaml        # Google Patents 模板
├── docs/                         # 文档
│   ├── PRD_ai_collection_platform.md    # 产品需求文档
│   ├── architecture_v2.md               # 架构设计 v2
│   ├── feature_spec_v3.md               # 功能规格 v3
│   ├── design_bigdata_platform.md       # 前端设计规范 (151KB)
│   ├── design_system.md                 # 设计系统
│   ├── ai_copilot_ux_flow.md            # AI Copilot 交互流程
│   └── ...                              # 项目管理文档
├── web-panel/                    # React 前端 (独立项目)
│   ├── src/
│   │   ├── pages/                # 6 个页面
│   │   ├── components/           # 公共组件
│   │   ├── layouts/              # 布局组件
│   │   ├── theme/                # 暗色/亮色主题
│   │   ├── services/             # API + Mock
│   │   ├── stores/               # Zustand 状态管理
│   │   └── hooks/                # 自定义 Hooks
│   └── package.json
├── docker-compose.yml            # Docker 编排 (10+ 服务)
├── Makefile                      # 快捷命令
├── requirements.txt              # Python 依赖
├── pyproject.toml                # 项目配置
└── .env.example                  # 环境变量示例
```

---

## ⚙️ 配置

复制 `.env.example` 为 `.env`，按需修改：

```bash
cp .env.example .env
```

### 关键配置项

```bash
# ── 日志 ──
SPIDER_LOG_LEVEL=INFO

# ── 采集并发 ──
SPIDER_MAX_CONCURRENT_TASKS=5

# ── 基础设施 ──
SPIDER_DB_URL=mongodb://localhost:27017       # MongoDB
SPIDER_PG_URL=postgresql+asyncpg://...        # PostgreSQL
SPIDER_KAFKA_BROKERS=localhost:9092           # Kafka
SPIDER_REDIS_URL=redis://localhost:6379/0     # Redis
SPIDER_MINIO_ENDPOINT=localhost:9000          # MinIO

# ── 功能开关 ──
SPIDER_DEDUP_ENABLED=true                     # Redis 去重
SPIDER_INCREMENTAL_MODE=true                  # 增量采集
SPIDER_ANTI_CRAWL_ENABLED=true                # 反爬模块
SPIDER_JINJA2_ENABLED=true                    # 动态模板
SPIDER_SCHEDULER_ENABLED=true                 # 任务调度

# ── ETL Topcs ──
SPIDER_ETL_RAW_TOPIC=spider-crawler
SPIDER_ETL_RDS_TOPIC=spider-rds-processed
SPIDER_ETL_ODS_TOPIC=spider-ods-processed
SPIDER_ETL_TASK_TOPIC=spider-task-processed
SPIDER_ETL_DWD_TOPIC=spider-dwd-processed
SPIDER_ETL_DWS_TOPIC=spider-dws-processed
SPIDER_ETL_DIM_TOPIC=spider-dim-processed
SPIDER_ETL_GRAPH_TOPIC=spider-graph-processed
SPIDER_ETL_ADS_TOPIC=spider-ads-processed
```

---

## 📋 Makefile 常用命令

```bash
# ── 安装 ──
make install          # 安装 Python 依赖
make install-dev      # 安装开发依赖

# ── 测试 ──
make test             # 运行全部测试
make test-unit        # 仅单元测试
make test-cov         # 覆盖率报告
make test-etl         # 运行 ETL 测试

# ── 代码质量 ──
make lint             # Ruff 检查
make format           # Ruff 格式化
make type-check       # mypy 类型检查
make check            # 全部质量检查

# ── 运行 ──
make run crawl google_patent     # 运行采集任务
make web                         # 启动 Web API
make etl-all                     # 启动全部 ETL Worker
make etl-rds                     # 启动 RDS Worker
make etl-ods                     # 启动 ODS Worker
make etl-dwd                     # 启动 DWD Worker

# ── Docker ──
make docker-up                   # 启动全部服务
make docker-down                 # 停止全部服务
make docker-logs                 # 查看日志
make docker-build                # 重新构建镜像
```

---

## 🧪 测试

```bash
# 全部测试
make test
# → 60+ 测试用例，0.5s 内全部通过

# 覆盖率
make test-cov
# → 覆盖率报告: htmlcov/index.html

# 仅 ETL 测试
make test-etl
# → 37 测试: 主题感知路由 + 双源合并 + 完整度门控 + 参数构建
```

### 测试覆盖

| 模块 | 测试数 | 说明 |
|------|--------|------|
| ETL Topic Dispatch | 16 | Topic-Aware 路由 & Handler 发现 |
| TsDwd Split | 21 | ODS/TASK 双源合并 + 完整度门控 |
| Web API | 30 | Kafka/Redis/ETL/Query/Handler/Scheduler |
| Parser | 8 | 模板解析 |
| Engine | 6 | 采集引擎 |

---

## 🔌 API 端点

启动 Web 面板后，访问 http://localhost:8000/docs 查看 Swagger 文档。

### 核心端点

| 方法 | 端点 | 说明 |
|------|------|------|
| `GET` | `/api/dashboard/metrics` | Dashboard 实时指标 |
| `GET` | `/api/dashboard2` | Dashboard v2 (WebSocket) |
| `GET` | `/api/tasks` | 任务列表 |
| `POST` | `/tasks/run` | 运行采集任务 |
| `POST` | `/tasks/schedule` | 调度任务 (Cron/Once) |
| `GET` | `/api/etl/layers` | ETL 层级列表 |
| `GET` | `/api/etl/{layer}/tables` | 层级下表列表 |
| `POST` | `/api/etl/query` | SQL 查询 (只读) |
| `GET` | `/api/etl/handlers/{layer}/{table}` | Handler 代码 |
| `PUT` | `/api/etl/handlers/{layer}/{table}` | 更新 Handler |
| `GET` | `/api/kafka/topics` | Kafka Topic 列表 |
| `GET` | `/api/kafka/offsets/{topic}` | Topic 消费位点 |
| `GET` | `/api/redis/offsets` | Redis 偏移量 |
| `GET` | `/api/scheduler/queue` | 调度队列 |
| `POST` | `/api/scheduler/enqueue` | 入队任务 |
| `GET` | `/api/data/{data_type}` | 查询数据 |
| `GET` | `/api/data/{data_type}/export.csv` | CSV 导出 |
| `WS` | `/api/monitor/ws` | 实时日志 WebSocket |

---

## 🎯 ETL 七层管道

```
Crawler 采集
  │ Kafka: spider-crawler
  ▼
🧬 RDS  — 原始数据入库     (Raw Data Store)
  │ Kafka: spider-rds-processed
  ▼
🧹 ODS  — 数据标准化       (Operational Data Store)
  │ Kafka: spider-ods-processed
  ▼
⚙️ TASK — AI 算法分析      (pdf_to_markdown / 实体提取等)
  │ Kafka: spider-task-processed
  ▼
📊 DWD  — 双源合并宽表     (ODS + TASK → 完整度门控 → Graph)
  │ Kafka: spider-dwd-processed ──→ spider-graph-processed
  ▼                          │
📈 DWS  — 汇总层           (聚合统计)      🌐 GRAPH — 知识图谱
  │ Kafka: spider-dws-processed
  ▼
🚀 ADS  — 应用层           (对外数据服务)
```

### DWD 完整度门控

ODS（专利元数据）和 TASK（算法结果）在 Kafka 中非同步到达。DWD 层通过完整度门控确保只有双方数据都就位时才推送到 Graph：

```
时序 A: ODS 先到 → INSERT (task_results=null) → ⏳ 不 emit
        TASK 后到 → UPDATE task_results → ✅ 完整 → emit Graph

时序 B: TASK 先到 → INSERT 最小记录 → ⏳ 不 emit
        ODS 后到 → UPDATE 专利字段 → ✅ 完整 → emit Graph
```

---

## 🛠️ 开发指南

### 添加新站点适配器

```python
# app/adapters/my_site.py
from app.adapters import BaseSiteAdapter, ADAPTER_REGISTRY

@ADAPTER_REGISTRY.register("my_site")
class MySiteAdapter(BaseSiteAdapter):
    async def before_list_page(self, page, **kwargs):
        # 翻页前操作
        pass
```

### 添加新模板

```yaml
# templates/my_site.yaml
name: my_site
data_type: article
adapter: my_site
display_name: "我的站点"
steps:
  list:
    url: "https://example.com/list?page={page}"
    pagination:
      selector: ".next-page"
```

### 添加新 ETL 层

继承 `ETLBase`，实现 `_handler_{table}` 方法，定义 `_producer_topic` 和 `_consumer_topics`。

---

## 📚 文档

| 文档 | 说明 |
|------|------|
| [PRD](docs/PRD_ai_collection_platform.md) | 产品需求文档 |
| [架构设计 v2](docs/architecture_v2.md) | 系统架构 |
| [功能规格 v3](docs/feature_spec_v3.md) | 功能模块规格 |
| [前端设计规范](docs/design_bigdata_platform.md) | 大数据平台 UI 设计 (151KB) |
| [设计系统](docs/design_system.md) | CSS 变量 + 组件 |
| [项目章程](docs/project_charter.md) | 项目章程 |
| [Sprint 计划](docs/sprint_plan.md) | 迭代计划 |
| [风险登记册](docs/risk_register.md) | 风险登记 |
| [AI Copilot 流程](docs/ai_copilot_ux_flow.md) | AI 助手交互设计 |

---

## 📄 License

MIT