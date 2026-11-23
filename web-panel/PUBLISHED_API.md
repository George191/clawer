# Published 与 Storage-backed 的外部 API

两类对象都通过 `Published APIs` 查看接口地址、`X-API-Key` 鉴权、有效期、数据范围和分页示例，并在 `SDK` 页下载 TypeScript、Python、Java 或 C 客户端。下载的是为选定接口生成的源码文件，密钥由调用方在运行时传入。

## Published 发布步骤

1. 项目管理员在 Explorer 中设置过滤条件；如只发布选中的对象，先勾选记录。
2. 打开 `Open in → Published APIs → Publish API`，填写名称、允许返回的属性和有效期，查看保存的数据范围并保存。
3. 在 `Overview → Manage API key` 中签发密钥。密钥只显示一次；轮换后旧密钥失效，撤销后停止新的读取请求。
4. 在 `Request` 页复制请求示例，或在 `SDK` 页选择语言并下载，按页面示例调用。

Published 的读取地址为 `GET /ontology/v1/publications/{publication_id}/data`，部署时使用 `api.$BACKEND_DOMAIN_SUFFIX`，仅接受 `limit`（1–100，默认 50）和 `offset`（0–100000，默认 0）。返回：

```json
{"items": [{"name": "示例"}], "hasMore": false, "nextOffset": null, "truncated": false}
```

属性名取决于发布时选择的属性 ID。继续请求响应中的 `nextOffset`，直到其为 `null`。若 `truncated=true`，表示仍有匹配记录，下一页超过允许的偏移量；发布者可缩小查询范围后继续读取。过滤条件与属性集合由发布者保存；调用方不能传入其他条件扩大范围。保存的是查询条件，匹配的数据会随对象变化更新；不是数据快照。

外部读取同时遵守发布者当前的对象、行和属性访问权限。发布者停用或失去项目管理权限后，接口不可用。模型版本变化时返回 409，管理员需刷新 Explorer，重新检查并保存发布。不存在、密钥无效、过期或已撤销的发布返回 404；缺少密钥返回 401；不支持的参数返回 422。

## Storage-backed

在 `Publish API` 中填写发布名称。已有发布可通过 `Overview → Rename publication` 改名，接口地址、范围和密钥保持原样。在 `Overview → Manage API key` 签发、轮换或撤销密钥，状态会在面板内更新。读取接口为 `GET /v1/etl/published/{publication_id}/data`（本地开发为 `/api/v1/etl/published/...`），分页上限为 `limit=500`、`offset=1000000`，返回格式沿用 `rows` 等原有字段。额外的 `search` 和 `filters` 只能缩小已发布范围。

## 客户端与部署

调用方在面板选择语言并点击下载，再按 `Usage example` 集成到自己的项目。当前下载项是单个源码文件，没有发布到包管理器。

- TypeScript：将 `publication-client.ts` 随 TypeScript 项目编译，运行时使用浏览器 fetch 或 Node.js 18+。不安全的大整数会明确报错。
- Python：将 `publication_client.py` 放在调用脚本旁并导入。只依赖 Python 3.10+ 标准库，保留 JSON 大整数。
- Java：用 `javac` 将 `PublicationClient.java` 与应用一同编译。需要 Java 11+，返回 JSON 文本。
- C：将 `publication_client.c` 用 C99 编译并链接 libcurl。调用方初始化 libcurl 并释放返回的 JSON 缓冲区。

SDK 对 HTTP 错误直接报错，不跟随重定向；TLS 证书校验保持开启。私有 CA 部署需配置调用环境的信任库，例如 Python 的 `SSL_CERT_FILE`、Node.js 的 `NODE_EXTRA_CA_CERTS` 或 Java 的 `javax.net.ssl.trustStore`。

在 `Published APIs → SDK` 的语言下拉框中选择 `User-token SDK (TypeScript)`，即可下载用户令牌版客户端，用于按调用账户权限访问对象、动作和函数。对象类型尚未创建 API 发布时，可直接在 `SDK` 标签页下载用户令牌版。外部发布密钥仅能读取授权范围。

部署时需将 Alembic 升级到 `8e35b4e1c682`（新增 `ontology_publications` 表），并重建包含后端和前端改动的镜像。
