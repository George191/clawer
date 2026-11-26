# Global Agents

Agent 是与 Automation 平级的全局基础设施。定义、模型连接、默认配置、运行队列和事件不归属于某一个产品。采集是一个已部署的执行器，不是所有 Agent 的能力边界。

## 存储和权限

使用现有应用数据库的 `app` schema，不创建采集专用数据库。表为 `agent_settings`、`agent_definitions`、`agent_connections`、`agent_shares`、`agent_runs` 和 `agent_events`。

- 私有 Agent：仅创建者可见、使用和编辑。
- 指定分享：填写一个或多个已注册用户的邮箱；接收者可查看和使用，不能编辑、删除、测试或重新绑定创建者的连接。
- 公开 Agent：所有已登录用户均可查看和使用。运行和事件始终归发起者所有，公开定义不公开其他人的执行结果。
- 系统 Agent：使用固定系统标识，没有个人所有者，向所有已登录用户提供。当前预置 `collection`（采集智能体），管理员在 **System settings → System agents** 配置默认模型、测试并启用。
- 模型连接始终归创建者所有；分享 Agent 不会分享连接管理权、API Key 或密文。密钥使用稳定的系统 `SECRET_KEY` 加密，不能由客户端指定 owner。

分享收回、定义修改、连接修改或系统默认配置变化后，排队运行会在执行前重新检查权限和快照。失败会明确记录，不回退到其他连接或模型。分享不会把接收者提升为编辑者。

## 公共接口

统一前缀 `/v1/agents`（由部署的 API 前缀配置控制），没有旧的 collection 专用接口别名。

| 接口 | 用途 |
|---|---|
| GET `/catalog` | 系统、自己创建、公开及指定分享的 Agent，以及自己的模型连接 |
| POST `/definitions`、PUT `/definitions/{id}` | 保存自己的定义和分享设置 |
| POST `/definitions/{id}/test` | 所有者测试模型连接，会使用模型额度 |
| POST `/connections`、PUT `/connections/{id}` | 保存自己的模型连接 |
| DELETE `/{definitions,connections}/{id}?revision=N` | 所有者删除相应资源 |
| GET、PUT `/settings`，POST `/settings/test` | 管理员配置和验证系统默认模型 |
| POST `/runs` | 提交运行，必须提供 `Idempotency-Key` 请求头 |
| GET `/runs?product_domain=ontology&limit=100` | 发起者按业务域查看运行；省略业务域查看全部 |
| GET `/runs/{id}`、GET `/runs/{id}/events?after=0&limit=100` | 发起者读取状态、产物和事件 |
| POST `/runs/{id}/cancel`、POST `/runs/{id}/resume` | 发起者取消或恢复失败运行 |

运行请求显式指定 Agent 和业务域，输入由对应执行器校验：

```json
{
  "agent_id": "c011ec70-0000-4000-8000-000000000001",
  "product_domain": "ai-collect",
  "input": {
    "url": "https://example.org/articles",
    "goal": "Collect article titles and publication dates",
    "max_pages": 20,
    "max_fields": 50
  }
}
```

`product_domain` 表示调用来源，不授予额外能力。当前提供 `assistant`（通用文本任务）和 `collection`（网页采集）两种执行器。个人 Agent 默认使用通用执行器，接收 `task` 与可选 `context`，不要求网页 URL；它只使用显式提供的上下文，不读取其他业务数据。新增执行器在 `agent/executors.py` 注册独立输入合约、工具、阶段和模型指令；公共存储和调度不假定采集输入或固定四个阶段。

## 执行约束

PostgreSQL 是队列、检查点和事件的唯一权威。租约 fencing、取消、重启恢复、幂等和事件序号由公共运行层负责；全局最多两条有效运行，每个用户最多三条排队/运行任务，每条任务最多领取三次。运行保存执行器阶段数量和配置快照，不使用进程内队列作为事实来源。

采集执行器保持 `capture → template → adapter → validate` 的固定流程。仅访问公共网页，生成采集模板和必要的适配器并做静态合约校验；不执行生成代码、不发布、不启动批量任务。页面内容是不可信证据，用户指令不能增加工具。模型通过 Responses API 调用；不回退到其他模型或 Chat Completions，原有请求预算和输出上限继续生效。

采集产物标记 `requires_review=true`、`published=false`。静态校验通过不等于真实采集验收。前端 Scout ChatPreview 仍为独立演示，保存 Agent 不会自动执行真实采集。

## 部署和迁移

在 `master` 提交、推送、拉取后重建 Web 镜像。镜像必须包含根目录 `agent/`。暂停旧 Web，再执行现有 Alembic 配置的 `upgrade head`（本次修订 `e38a62f9c104`），最后启动新 Web。

迁移将现有 `collection_agent_*` 表、索引和约束原位改名，保留配置密文、运行 ID、检查点和事件，补充运行业务域、执行器与阶段数量，并注册系统采集 Agent。旧请求被转换为统一运行合约并更新幂等摘要；不在生产运行代码中保留历史命名分支。分享权限无法无损表示为旧结构，因此降级必须先显式迁移数据。

## 开发验证

```sh
python -m unittest agent.tests.test_harness agent.tests.test_api
```

PostgreSQL 集成测试仅允许显式指定的独立可销毁 `agent_test` 数据库，测试会重建其中的 `app` schema，拒绝其他数据库名。

```sh
AGENT_TEST_DATABASE_URL=postgresql+asyncpg://test:test@127.0.0.1:55432/agent_test \
  python -m unittest agent.tests.test_store_postgres
```
