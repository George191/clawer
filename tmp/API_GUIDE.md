
# NGA Broadcast Warn API 使用指南

## API 端点

Base URL: `https://msi.nga.mil`

### 主要端点

```
1. GET /api/publications/broadcast-warn/current-warnings
   - 获取当前告警

2. GET /api/publications/broadcast-warn/inforce
   - 获取有效告警

3. GET /api/publications/broadcast-warn/navareas
   - 获取航区列表

4. GET /api/publications/broadcast-warn/subregions
   - 获取子区域列表

5. GET /api/publications/broadcast-warn
   - 查询广播告警（带参数）
```

### 查询参数

```
status: string              - 状态
navArea: string             - 航区
subregion: string           - 子区域
msgNumber: integer          - 消息编号
msgNumberStart: integer     - 消息编号范围（开始）
msgNumberEnd: integer       - 消息编号范围（结束）
msgYear: integer            - 消息年份
msgYearStart: integer       - 年份范围（开始）
msgYearEnd: integer         - 年份范围（结束）
issueDateStart: dateTime    - 发布日期范围（开始）
issueDateEnd: dateTime      - 发布日期范围（结束）
output: string              - 输出格式
```

## 使用示例

### Python 代码示例

```python
import httpx

async def get_current_warnings():
    async with httpx.AsyncClient(follow_redirects=True) as client:
        response = await client.get(
            "https://msi.nga.mil/api/publications/broadcast-warn/current-warnings"
        )
        return response.json()

async def query_by_year(year: int):
    async with httpx.AsyncClient(follow_redirects=True) as client:
        response = await client.get(
            "https://msi.nga.mil/api/publications/broadcast-warn",
            params={'msgYear': year}
        )
        return response.json()
```

## 文件说明

### 1. nga_real_api_test.py
- 测试真实 NGA API 端点
- 自动保存结果到 JSON 文件

### 2. nga_test_clean.py
- 模拟数据测试
- 演示数据解析和分析逻辑

### 3. nga_broadcast_warn_test.py
- 完整功能版本
- 包含数据结构定义和分析器

## 数据结构

### 告警数据字段

```json
{
  "id": "告警ID",
  "title": "告警标题",
  "description": "描述",
  "severity": "严重程度 (low/medium/high/critical)",
  "location": "位置信息",
  "type": "类型",
  "issueDate": "发布时间",
  "expiryDate": "过期时间",
  "navArea": "航区",
  "subregion": "子区域",
  "msgNumber": "消息编号",
  "msgYear": "年份"
}
```

## 下一步

1. 运行 `nga_real_api_test.py` 测试 API 连通性
2. 根据返回的数据结构调整解析逻辑
3. 集成到您的项目中

## 注意事项

- API 可能有访问限制或需要认证
- 建议在生产环境中添加错误处理和重试逻辑
- 请遵守 NGA API 的使用条款
