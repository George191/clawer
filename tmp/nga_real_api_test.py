
"""
NGA Broadcast Warn API 真实端点测试
使用实际的 NGA API 端点
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any

import httpx

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("NGA_API")


class NGARealAPI:
    """NGA 真实 API 客户端"""

    BASE_URL = "https://msi.nga.mil"

    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)

    async def close(self):
        await self.client.aclose()

    async def test_endpoint(self, endpoint: str, params: Dict = None) -> Optional[Dict]:
        """测试单个端点"""
        url = f"{self.BASE_URL}{endpoint}"
        try:
            logger.info(f"Testing: {url}")
            response = await self.client.get(url, params=params or {})
            logger.info(f"Status: {response.status_code}")

            if response.status_code == 200:
                try:
                    return response.json()
                except json.JSONDecodeError:
                    content_type = response.headers.get('Content-Type', '')
                    logger.info(f"Content-Type: {content_type}")
                    # 返回文本内容的预览
                    return {'_text_preview': response.text[:500]}

            return {'_status_code': response.status_code, '_url': url}

        except Exception as e:
            logger.error(f"Error accessing {url}: {e}")
            return {'_error': str(e), '_url': url}

    async def get_current_warnings(self) -> Dict:
        """获取当前告警"""
        return await self.test_endpoint("/api/publications/broadcast-warn/current-warnings")

    async def get_inforce(self) -> Dict:
        """获取有效告警"""
        return await self.test_endpoint("/api/publications/broadcast-warn/inforce")

    async def get_navareas(self) -> Dict:
        """获取航区列表"""
        return await self.test_endpoint("/api/publications/broadcast-warn/navareas")

    async def get_subregions(self) -> Dict:
        """获取子区域列表"""
        return await self.test_endpoint("/api/publications/broadcast-warn/subregions")

    async def query_broadcast_warn(self, **kwargs) -> Dict:
        """查询广播告警

        可选参数:
            status: string
            navArea: string
            subregion: string
            msgNumber: integer
            msgNumberStart: integer
            msgNumberEnd: integer
            msgYear: integer
            msgYearStart: integer
            msgYearEnd: integer
            issueDateStart: dateTime
            issueDateEnd: dateTime
            output: string
        """
        return await self.test_endpoint("/api/publications/broadcast-warn", kwargs)


async def main():
    print("=" * 80)
    print("NGA Broadcast Warn API - Real Endpoint Test")
    print("=" * 80)
    print(f"Test Time: {datetime.now().isoformat()}")
    print()

    api = NGARealAPI()
    results = {}

    try:
        # 测试各个端点
        endpoints = [
            ('Current Warnings', api.get_current_warnings),
            ('In Force', api.get_inforce),
            ('Nav Areas', api.get_navareas),
            ('Subregions', api.get_subregions),
        ]

        for name, func in endpoints:
            print(f"\n[Test] {name}")
            print("-" * 60)
            result = await func()
            results[name] = result

            if result:
                if '_error' in result:
                    print(f"[ERROR] {result['_error']}")
                elif '_status_code' in result:
                    print(f"[STATUS] {result['_status_code']}")
                elif '_text_preview' in result:
                    print(f"[CONTENT] {result['_text_preview']}...")
                else:
                    print(f"[OK] Got data")
                    print(f"  Data type: {type(result)}")
                    if isinstance(result, list):
                        print(f"  Items: {len(result)}")
                    elif isinstance(result, dict):
                        print(f"  Keys: {list(result.keys())[:10]}")

        # 测试查询功能
        print(f"\n[Test] Query with parameters")
        print("-" * 60)
        query_result = await api.query_broadcast_warn(msgYear=2024)
        results['Query Test'] = query_result
        print(f"Query test complete")

        # 保存所有结果
        output = {
            'test_time': datetime.now().isoformat(),
            'base_url': api.BASE_URL,
            'endpoints_tested': list(results.keys()),
            'results': results
        }

        output_file = "tmp/nga_real_api_results.json"
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(output, f, ensure_ascii=False, indent=2)

        print(f"\n[OK] Results saved to: {output_file}")

    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
    finally:
        await api.close()

    print("\n" + "=" * 80)
    print("Test Complete")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
