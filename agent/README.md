# Collection Harness

仅用于公共网页采集取证、采集模板生成、必要的适配器生成和静态合约校验。不是通用聊天或代码执行 Agent。固定工具路径为 `capture → template → adapter → validate`；模型无法增加工具、执行命令、访问数据库、运行生成代码、发布模板或启动批量任务。

采集页面的 `ChatPreview` 仍为独立样式演示，尚未接入本 API。不要把演示步骤当成真实执行记录。

## 配置与启用

1. 通过 Git 部署代码，使用现有 Alembic 配置升级到 `c801b426df90`，再启动 Web。`docker/web.Dockerfile` 包含根目录 `agent/` 包。运行期间不创建或修改数据库结构。
2. 管理员打开 **Settings → Collection Agent**。默认模型为 `gpt-6-sol`，推理强度 `high`，端点 `https://api.openai.com/v1`；支持更换兼容 OpenAI **Responses API** 的 HTTPS 端点和模型，不回退到其他模型或 Chat Completions。
3. 保持禁用状态，输入 API Key 并保存。密钥使用系统已有、显式配置的持久化 `SECRET_KEY` 派生密钥加密；没有稳定系统密钥时拒绝保存。密钥不返回前端、不进入运行快照和事件，不写入浏览器存储。留空保留，显式勾选清除才删除。
4. 点击 **Test connection**。这是实际模型请求，会使用模型额度；只有请求完成且配置修订号仍一致才记录验证成功。
5. 验证成功后启用并保存。连接、模型、推理强度、超时、输出预算或密钥变更均需要重新验证；单独切换启停保留验证结果。服务默认禁用，缺少配置/验证时提交运行返回 503。

Settings 使用修订号防止并发覆盖。修改系统 `SECRET_KEY` 后需要重新输入 API Key；不要让多个 Web 实例使用不同系统密钥。已排队运行固定配置修订号，配置变更后拒绝混用新密钥和旧端点，应重新提交新运行。已经开始的运行使用其加载的配置。

## API

公网前缀沿用项目配置，浏览器入口为 `/api/v1/agents/collection`。所有接口需要现有登录 Bearer token；Settings 读取、修改与连接测试仅超级管理员可用。运行和事件严格按当前用户隔离，无法通过客户端传入其他 owner。

| 方法 | 路径 | 行为 |
|---|---|---|
| GET / PUT | `/settings` | 脱敏读取 / 乐观锁更新配置 |
| POST | `/settings/test` | 验证已保存模型连接 |
| POST | `/runs` | 持久化提交；必须提供 `Idempotency-Key` 请求头 |
| GET | `/runs/{id}` | 状态、配置快照、检查点位置、结果与错误码 |
| GET | `/runs/{id}/events?after=0&limit=100` | 按递增序号回放真实进度，支持断线续读 |
| POST | `/runs/{id}/cancel` | 撤销运行租约，阻止后续结果提交；重复取消幂等 |
| POST | `/runs/{id}/resume` | 仅恢复失败且尚有尝试预算的运行 |

创建请求：

```json
{
  "url": "https://example.org/articles",
  "goal": "Collect article titles and publication dates",
  "max_pages": 20,
  "max_fields": 50
}
```

同一用户、同一幂等键和请求返回同一个运行；同键不同请求返回 409。完成结果为 `template_yaml`、`adapter_python`、来源证据摘要哈希及产物哈希。`requires_review=true`、`published=false`，静态检查通过不代表适配器执行或试采集通过。发布与调度继续使用已有独立流程；本 Harness 不修改 MinIO 对象或既有模板绑定。

## 执行保证与界限

- PostgreSQL 同时保存队列、检查点和顺序事件。阶段推进和事件在同一事务提交，成功只发生在最终校验检查点之后。没有内存队列或本地 JSON 作为权威状态。
- 每次领取使用唯一租约 token，45 秒有效、每 10 秒续约。全局最多两个有效运行，每个 Web 进程一个执行循环。过期租约可被其他进程领取；旧执行者不能续租、推进检查点或提交成功。
- 重启从最后提交阶段继续；同一次运行终生最多领取三次，手动恢复也不重置预算。没有检查点的浏览器/模型阶段可能重做，模型计费不保证恰好一次。只有读取网页和生成草稿可以重做；发布等外部写入不在重试边界内。
- 每次领取最多四次逻辑模型生成，每次最多两次 HTTP 尝试；只重试明确的瞬时 HTTP 状态。超时请求可能已被提供商接收，因此不自动重发。单次模型输出上限 16,384 token，输入 128 KiB、供应商响应封装 512 KiB、输出产物 64 KiB，总检查点 512 KiB。每次 HTTP 请求包括响应体读取都受配置总时限限制。最多 100 页、100 个字段，具体请求可进一步收紧。
- 取消立即写入数据库并撤销租约，活动工具在下一次心跳检测到撤销后取消。浏览器由既有 renderer 管理，其关闭可能等待当前浏览器超时；取消后不能提交结果，也不会假称网络请求已经瞬间终止。
- 输入和重定向 URL 经过公共地址校验。实际浏览器流量继续通过既有 ProxyTunnel 的目的地址/DNS 检查；不提供任意 HTTP、Shell、文件或数据库工具。模型上下文中的网页内容作为不可信证据，不能扩大工具权限。生成 URL 必须属于已观察来源，生成代码只做静态校验、不执行。生成 YAML 在进入现有解析器前拒绝锚点和别名，避免小体积输入造成指数级遍历。
- 所有模型错误、拒绝、不完整输出、非法合约、预算超限和基础设施故障明确失败。事件只含阶段、状态和错误码；不输出密钥、完整模型推理或原始供应商错误正文。
- 连接验证只验证传输、认证和模型响应，不能代替采集质量验收。启用前必须针对实际来源运行完整任务并人工审查模板/字段，之后才进入原有发布流程。尚未完成真实模型和实际来源验证时，不应宣称业务已通过投产验收。

## 验证

```powershell
python -m unittest agent.tests.test_harness agent.tests.test_api app.web.tests.test_scout_gateway
```

真实 PostgreSQL 集成测试使用 `AGENT_TEST_DATABASE_URL` 指定独立、可销毁的数据库，名称必须为 `collection_agent_test`；测试会重置该数据库中的 `app` schema，拒绝其他数据库名。不要配置生产连接。

```powershell
$env:AGENT_TEST_DATABASE_URL = 'postgresql+asyncpg://test:test@127.0.0.1:55432/collection_agent_test'
python -m unittest agent.tests.test_store_postgres
```

覆盖幂等并发、全局并发上限、用户隔离、取消 fencing、过期执行者、检查点/事件原子性、重启恢复、尝试预算、设置修订、密钥加密和跨实例读取。模型协议测试使用 MockTransport，不调用或消耗真实模型额度。

诊断使用运行 UUID、`error_code`、事件序号和时间戳。`failed` 不会变成替代结果；基础设施暂时不可用时保留租约恢复路径。没有自动删除运行/检查点的后台任务，清理必须显式遵守项目的保留规则。

## 设计依据

- [OpenAI GPT-6 Sol 模型规格](https://developers.openai.com/api/docs/models/gpt-6-sol) 与 [推理参数](https://developers.openai.com/api/docs/guides/reasoning)：使用实际模型 ID 和 Responses `reasoning.effort`。
- [Anthropic 长任务 Harness 设计](https://www.anthropic.com/research/effective-harnesses-for-long-running-agents)：可验证阶段与持久化进展。
- [LangGraph 故障容错原始文档](https://github.com/langchain-ai/docs/blob/main/src/oss/langgraph/fault-tolerance.mdx)：重放按至少一次语义处理，不把不可幂等发布写入重试阶段。本实现使用项目现有 PostgreSQL/asyncio 固定流程，无新增 Agent 框架依赖。
