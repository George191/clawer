
"""
NGA Broadcast Warn API 简化版测试
快速上手测试
"""

import asyncio
import json
import httpx
from datetime import datetime


async def main():
    print("=" * 60)
    print("NGA Broadcast Warn 简化测试")
    print("=" * 60)
    print()

    # 测试 1: 尝试访问 NGA MSI 网站
    print("测试 1: 访问 NGA Maritime Safety Information 网站")
    print("-" * 60)

    urls = [
        "https://msi.nga.mil",
        "https://msi.nga.mil/api",
        "https://www.nga.mil"
    ]

    for url in urls:
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                response = await client.get(url)
                print(f"{url}")
                print(f"  状态码: {response.status_code}")
                print(f"  Content-Type: {response.headers.get('Content-Type', 'unknown')}")
        except Exception as e:
            print(f"{url}")
            print(f"  错误: {e}")
        print()

    # 测试 2: 保存测试数据
    print("测试 2: 生成测试数据")
    print("-" * 60)

    test_data = {
        "test_time": datetime.now().isoformat(),
        "test_name": "NGA Broadcast Warn API",
        "notes": [
            "NGA (National Geospatial-Intelligence Agency)",
            "MSI (Maritime Safety Information)",
            "Broadcast Warnings - 海事安全广播告警"
        ],
        "possible_endpoints": [
            "/api/broadcast/warnings",
            "/broadcast/warn",
            "/msi/warnings",
            "/api/warnings",
            "/publications"
        ],
        "related_resources": [
            "https://msi.nga.mil/NGAPortal/MSI.portal",
            "https://www.maritime.dot.gov/msci"
        ],
        "sample_warning": {
            "id": "WARN-2024-001",
            "title": "海事安全警告示例",
            "description": "这是一个示例告警数据结构",
            "severity": "high",
            "location": "示例位置",
            "type": "navigation",
            "issued": datetime.now().isoformat()
        }
    }

    output_file = "tmp/simple_test_results.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(test_data, f, ensure_ascii=False, indent=2)

    print(f"✅ 测试数据已保存到: {output_file}")
    print()

    # 测试 3: 显示数据结构
    print("测试 3: 告警数据结构示例")
    print("-" * 60)

    print("\n告警数据字段示例:")
    print("  - id: 告警唯一标识符")
    print("  - title: 告警标题")
    print("  - description: 告警描述")
    print("  - severity: 严重程度 (low/medium/high/critical)")
    print("  - location: 地理位置")
    print("  - coordinates: 坐标 {lat, lon}")
    print("  - type: 告警类型 (weather/piracy/navigation)")
    print("  - issued: 发布时间")
    print("  - expires: 过期时间")

    print()
    print("=" * 60)
    print("测试完成")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
