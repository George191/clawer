
"""
NGA Broadcast Warn API 测试脚本
针对美国国家地理空间情报局的告警信息 API 进行测试
独立运行，不影响其他代码
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict

import httpx

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("NGA_Broadcast_Warn")


@dataclass
class NGABroadcastWarning:
    """NGA 广播告警数据结构"""
    warning_id: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    issue_date: Optional[str] = None
    expiry_date: Optional[str] = None
    severity: Optional[str] = None
    location: Optional[str] = None
    coordinates: Optional[Dict[str, float]] = None
    warning_type: Optional[str] = None
    source: str = "NGA"
    raw_data: Optional[Dict] = None


class NGABroadcastWarnAPI:
    """NGA 广播告警 API 客户端"""

    # NGA MSI (Maritime Safety Information) API 基础 URL
    BASE_URL = "https://msi.nga.mil/api"

    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.client = httpx.AsyncClient(timeout=timeout)

    async def close(self):
        """关闭 HTTP 客户端"""
        await self.client.aclose()

    async def test_connection(self) -> bool:
        """测试 API 连接"""
        logger.info("测试 NGA API 连接...")
        try:
            # 尝试访问 NGA MSI 的公开端点
            response = await self.client.get(
                f"{self.BASE_URL}/publications",
                follow_redirects=True
            )
            logger.info(f"连接状态: {response.status_code}")
            return response.status_code == 200
        except Exception as e:
            logger.error(f"连接失败: {e}")
            return False

    async def get_broadcast_warnings(self, **kwargs) -> List[Dict[str, Any]]:
        """
        获取广播告警信息

        Args:
            **kwargs: 查询参数，如 limit, offset, type, region 等

        Returns:
            告警列表
        """
        warnings = []
        try:
            # 尝试多个可能的端点
            endpoints = [
                "/broadcast/warnings",
                "/broadcast/warn",
                "/warnings/broadcast",
                "/msi/warnings",
                "/api/warnings"
            ]

            for endpoint in endpoints:
                try:
                    url = f"{self.BASE_URL}{endpoint}"
                    logger.info(f"尝试访问: {url}")

                    response = await self.client.get(
                        url,
                        params=kwargs,
                        follow_redirects=True
                    )

                    if response.status_code == 200:
                        try:
                            data = response.json()
                            if isinstance(data, list):
                                warnings.extend(data)
                            elif isinstance(data, dict):
                                if 'warnings' in data:
                                    warnings.extend(data['warnings'])
                                elif 'results' in data:
                                    warnings.extend(data['results'])
                                else:
                                    warnings.append(data)

                            if warnings:
                                logger.info(f"从 {endpoint} 获取到 {len(warnings)} 条告警")
                                break
                        except json.JSONDecodeError:
                            # 如果不是 JSON，可能是 HTML，尝试解析
                            logger.info(f"响应不是 JSON，尝试其他端点...")
                            continue
                except Exception as e:
                    logger.debug(f"端点 {endpoint} 失败: {e}")
                    continue

        except Exception as e:
            logger.error(f"获取告警失败: {e}")

        return warnings

    async def get_publications_list(self) -> List[Dict[str, Any]]:
        """获取 NGA 出版物列表"""
        publications = []
        try:
            response = await self.client.get(
                f"{self.BASE_URL}/publications",
                follow_redirects=True
            )

            if response.status_code == 200:
                try:
                    data = response.json()
                    if isinstance(data, list):
                        publications = data
                    elif isinstance(data, dict) and 'publications' in data:
                        publications = data['publications']
                except json.JSONDecodeError:
                    logger.info("出版物响应不是 JSON 格式")

        except Exception as e:
            logger.error(f"获取出版物列表失败: {e}")

        return publications

    async def parse_warning_data(self, raw_data: Dict[str, Any]) -> Optional[NGABroadcastWarning]:
        """解析原始告警数据"""
        try:
            warning = NGABroadcastWarning(
                raw_data=raw_data
            )

            # 尝试从常见字段中提取信息
            field_mappings = {
                'warning_id': ['id', 'warningId', 'identifier', 'uid'],
                'title': ['title', 'subject', 'headline', 'name'],
                'description': ['description', 'text', 'content', 'message'],
                'issue_date': ['issueDate', 'issued', 'date', 'created', 'timestamp'],
                'expiry_date': ['expiryDate', 'expires', 'validUntil'],
                'severity': ['severity', 'priority', 'level', 'urgency'],
                'location': ['location', 'area', 'region', 'place'],
                'warning_type': ['type', 'category', 'kind']
            }

            for target_field, possible_fields in field_mappings.items():
                for field in possible_fields:
                    if field in raw_data and raw_data[field] is not None:
                        setattr(warning, target_field, raw_data[field])
                        break

            # 尝试提取坐标
            coord_fields = ['coordinates', 'lat', 'lon', 'latitude', 'longitude', 'geo']
            for field in coord_fields:
                if field in raw_data:
                    val = raw_data[field]
                    if isinstance(val, dict):
                        warning.coordinates = val
                    elif isinstance(val, list) and len(val) >= 2:
                        warning.coordinates = {'lat': val[0], 'lon': val[1]}

            return warning
        except Exception as e:
            logger.error(f"解析告警数据失败: {e}")
            return None


class NGAWarningAnalyzer:
    """NGA 告警分析器"""

    def __init__(self):
        self.warnings: List[NGABroadcastWarning] = []

    def add_warning(self, warning: NGABroadcastWarning):
        """添加告警"""
        self.warnings.append(warning)

    def analyze_severity_distribution(self) -> Dict[str, int]:
        """分析严重程度分布"""
        distribution = {}
        for warning in self.warnings:
            severity = warning.severity or "unknown"
            distribution[severity] = distribution.get(severity, 0) + 1
        return distribution

    def analyze_by_type(self) -> Dict[str, int]:
        """按类型分析"""
        distribution = {}
        for warning in self.warnings:
            warn_type = warning.warning_type or "unknown"
            distribution[warn_type] = distribution.get(warn_type, 0) + 1
        return distribution

    def get_recent_warnings(self, days: int = 30) -> List[NGABroadcastWarning]:
        """获取近期告警"""
        # 简单实现：返回所有告警
        return self.warnings


async def run_comprehensive_test():
    """运行综合测试"""
    print("=" * 80)
    print("NGA Broadcast Warn API 综合测试")
    print("=" * 80)
    print(f"测试时间: {datetime.now().isoformat()}")
    print()

    api = NGABroadcastWarnAPI()
    analyzer = NGAWarningAnalyzer()

    try:
        # 测试 1: 连接测试
        print("测试 1: API 连接测试")
        print("-" * 60)
        connection_ok = await api.test_connection()
        print(f"连接状态: {'✅ 成功' if connection_ok else '❌ 失败'}")
        print()

        # 测试 2: 获取广播告警
        print("测试 2: 获取广播告警")
        print("-" * 60)
        warnings = await api.get_broadcast_warnings(limit=50)

        if warnings:
            print(f"✅ 获取到 {len(warnings)} 条告警")

            # 解析告警
            parsed_count = 0
            for raw_warning in warnings[:10]:  # 只解析前10条作为示例
                parsed = await api.parse_warning_data(raw_warning)
                if parsed:
                    analyzer.add_warning(parsed)
                    parsed_count += 1

            print(f"✅ 成功解析 {parsed_count} 条告警")

            # 显示前3条告警详情
            print("\n前3条告警详情:")
            for i, warning in enumerate(analyzer.warnings[:3]):
                print(f"\n  [{i+1}] {warning.title or '无标题'}")
                print(f"      严重程度: {warning.severity or '未知'}")
                print(f"      类型: {warning.warning_type or '未知'}")
                print(f"      位置: {warning.location or '未知'}")
        else:
            print("ℹ️  未获取到告警数据（可能是 API 访问限制）")
            print()

            # 测试 3: 获取出版物列表作为替代
            print("测试 3: 获取 NGA 出版物列表")
            print("-" * 60)
            publications = await api.get_publications_list()
            if publications:
                print(f"✅ 获取到 {len(publications)} 个出版物")
                print("\n出版物示例:")
                for pub in publications[:3]:
                    print(f"  - {pub.get('title', pub.get('name', '未知'))}")
            else:
                print("ℹ️  未获取到出版物数据")

        # 分析数据
        if analyzer.warnings:
            print("\n测试 4: 告警数据分析")
            print("-" * 60)

            severity_dist = analyzer.analyze_severity_distribution()
            print(f"严重程度分布: {severity_dist}")

            type_dist = analyzer.analyze_by_type()
            print(f"类型分布: {type_dist}")

        # 保存原始数据到文件
        print("\n测试 5: 保存测试数据")
        print("-" * 60)

        test_data = {
            'test_time': datetime.now().isoformat(),
            'warnings_count': len(warnings),
            'warnings': warnings[:20],  # 保存前20条
            'analyzed_count': len(analyzer.warnings),
            'analysis': {
                'severity_distribution': analyzer.analyze_severity_distribution(),
                'type_distribution': analyzer.analyze_by_type()
            }
        }

        output_file = "tmp/nga_test_results.json"
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(test_data, f, ensure_ascii=False, indent=2)

        print(f"✅ 测试数据已保存到: {output_file}")

    except Exception as e:
        logger.error(f"测试过程出错: {e}", exc_info=True)
    finally:
        await api.close()

    print("\n" + "=" * 80)
    print("测试完成")
    print("=" * 80)


async def run_alternative_test():
    """运行替代测试（模拟 NGA 告警 API 响应）"""
    print("=" * 80)
    print("NGA Broadcast Warn 模拟测试")
    print("=" * 80)
    print("说明: 此测试模拟 NGA 告警 API 的响应格式")
    print()

    analyzer = NGAWarningAnalyzer()

    # 模拟告警数据
    mock_warnings = [
        {
            'id': 'WARN-001',
            'title': '海盗活动警告 - 亚丁湾',
            'description': '在亚丁湾东部海域报告有海盗活动，建议船只绕行',
            'issueDate': '2024-06-01T10:00:00Z',
            'expiryDate': '2024-06-10T10:00:00Z',
            'severity': 'high',
            'location': '亚丁湾',
            'coordinates': {'lat': 13.0, 'lon': 50.0},
            'type': 'piracy'
        },
        {
            'id': 'WARN-002',
            'title': '恶劣天气警报 - 北大西洋',
            'description': '预计未来48小时内将有暴风雨，风速可达30m/s',
            'issueDate': '2024-06-02T08:00:00Z',
            'expiryDate': '2024-06-04T08:00:00Z',
            'severity': 'medium',
            'location': '北大西洋',
            'coordinates': {'lat': 45.0, 'lon': -30.0},
            'type': 'weather'
        },
        {
            'id': 'WARN-003',
            'title': '海事安全通知 - 马六甲海峡',
            'description': '马六甲海峡有临时航行限制，请查看最新航海图',
            'issueDate': '2024-06-03T12:00:00Z',
            'expiryDate': '2024-06-15T12:00:00Z',
            'severity': 'low',
            'location': '马六甲海峡',
            'coordinates': {'lat': 2.0, 'lon': 101.5},
            'type': 'navigation'
        }
    ]

    print(f"模拟 {len(mock_warnings)} 条告警数据")
    print()

    # 解析模拟数据
    api = NGABroadcastWarnAPI()
    parsed_count = 0
    for raw_warning in mock_warnings:
        parsed = await api.parse_warning_data(raw_warning)
        if parsed:
            analyzer.add_warning(parsed)
            parsed_count += 1
            print(f"[OK] 解析告警: {parsed.title}")

    print()
    print("分析结果:")
    print("-" * 60)

    severity_dist = analyzer.analyze_severity_distribution()
    print(f"严重程度分布: {severity_dist}")

    type_dist = analyzer.analyze_by_type()
    print(f"类型分布: {type_dist}")

    # 保存模拟数据
    test_data = {
        'test_time': datetime.now().isoformat(),
        'test_type': 'mock',
        'warnings_count': len(mock_warnings),
        'warnings': mock_warnings,
        'analysis': {
            'severity_distribution': severity_dist,
            'type_distribution': type_dist
        }
    }

    output_file = "tmp/nga_mock_test_results.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(test_data, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 模拟测试数据已保存到: {output_file}")

    await api.close()

    print("\n" + "=" * 80)
    print("模拟测试完成")
    print("=" * 80)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--mock":
        asyncio.run(run_alternative_test())
    else:
        print("选择测试模式:")
        print("1. 真实 API 测试 (可能因访问限制而失败)")
        print("2. 模拟数据测试 (推荐)")
        print()

        choice = input("请选择 (1 或 2，默认 2): ").strip()

        if choice == "1":
            asyncio.run(run_comprehensive_test())
        else:
            asyncio.run(run_alternative_test())
