
# NGA Broadcast Warn API 测试

此目录包含针对美国国家地理空间情报局（NGA）广播告警信息 API 的测试代码。

## 文件说明

```
tmp/
├── README.md                          # 本文件
├── nga_broadcast_warn_test.py         # 完整测试脚本
├── simple_nga_test.py                 # 简化版测试
├── nga_test_clean.py                  # 无 emoji 版本测试
├── nga_real_api_test.py              # [NEW] 真实 API 端点测试
└── (测试运行后会生成) *.json         # 测试结果数据
```

## 快速开始

### 1. 运行真实 API 端点测试（推荐）

```bash
python tmp/nga_real_api_test.py
```

### 2. 运行模拟测试

```bash
python tmp/nga_test_clean.py
```

### 3. 运行简化测试

```bash
python tmp/simple_nga_test.py
```

## NGA 背景信息

**NGA (National Geospatial-Intelligence Agency)** - 美国国家地理空间情报局

**MSI (Maritime Safety Information)** - 海事安全信息

主要功能：
- 提供海事安全广播告警
- 出版海图和航海指南
- 通过 GMDSS（全球海上遇险与安全系统）广播信息

## 相关资源

- NGA MSI 门户: https://msi.nga.mil
- 美国海事咨询系统: https://www.maritime.dot.gov/msci
- NGA 官网: https://www.nga.mil

## 测试内容

### 完整测试 (nga_broadcast_warn_test.py)

包含以下测试：

1. **API 连接测试** - 测试与 NGA API 的连接
2. **获取广播告警** - 尝试从多个端点获取告警数据
3. **获取出版物列表** - 获取 NGA 出版物
4. **告警数据分析** - 分析严重程度和类型分布
5. **保存测试数据** - 将结果保存到 JSON 文件

### 简化测试 (simple_nga_test.py)

包含：
- 网站连通性测试
- 测试数据生成
- API 端点说明

## 数据结构

### NGABroadcastWarning (告警数据)

```python
{
    "warning_id": "唯一标识符",
    "title": "告警标题",
    "description": "告警描述",
    "issue_date": "发布时间",
    "expiry_date": "过期时间",
    "severity": "严重程度 (low/medium/high)",
    "location": "位置信息",
    "coordinates": {"lat": 纬度, "lon": 经度},
    "warning_type": "类型 (weather/piracy/navigation)",
    "source": "NGA",
    "raw_data": {原始数据}
}
```

## 注意事项

1. **API 访问限制** - NGA API 可能有访问限制或需要认证
2. **独立运行** - 此目录代码完全独立，不影响项目其他部分
3. **测试数据** - 生成的 JSON 文件保存在 `tmp/` 目录下

## 故障排除

### 如果无法访问 NGA API

- 使用 `--mock` 模式运行完整测试
- 使用简化测试脚本
- 访问 NGA 官网了解最新 API 信息

### 依赖安装

```bash
pip install httpx
```

## 下一步

1. 运行简化测试了解基本情况
2. 使用模拟模式测试数据处理逻辑
3. 根据实际需求调整和扩展功能
