
"""
NGA Broadcast Warn API 测试脚本（无 emoji 版本）
独立运行，不影响其他代码
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict

import httpx

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

    BASE_URL = "https://msi.nga.mil/api"

    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.client = httpx.AsyncClient(timeout=timeout)

    async def close(self):
        await self.client.aclose()

    async def test_connection(self) -> bool:
        logger.info("Testing NGA API connection...")
        try:
            response = await self.client.get(
                f"{self.BASE_URL}/publications",
                follow_redirects=True
            )
            logger.info(f"Connection status: {response.status_code}")
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Connection failed: {e}")
            return False

    async def get_broadcast_warnings(self, **kwargs) -> List[Dict[str, Any]]:
        warnings = []
        try:
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
                    logger.info(f"Trying: {url}")

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
                                logger.info(f"Got {len(warnings)} warnings from {endpoint}")
                                break
                        except json.JSONDecodeError:
                            continue
                except Exception as e:
                    continue

        except Exception as e:
            logger.error(f"Failed to get warnings: {e}")

        return warnings

    async def parse_warning_data(self, raw_data: Dict[str, Any]) -> Optional[NGABroadcastWarning]:
        try:
            warning = NGABroadcastWarning(raw_data=raw_data)

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
            logger.error(f"Failed to parse warning: {e}")
            return None


async def main():
    print("=" * 80)
    print("NGA Broadcast Warn API Test")
    print("=" * 80)
    print(f"Test Time: {datetime.now().isoformat()}")
    print()

    print("Running mock test mode...")
    print()

    mock_warnings = [
        {
            'id': 'WARN-001',
            'title': 'Piracy Warning - Gulf of Aden',
            'description': 'Pirate activity reported in eastern Gulf of Aden',
            'issueDate': '2024-06-01T10:00:00Z',
            'severity': 'high',
            'location': 'Gulf of Aden',
            'type': 'piracy'
        },
        {
            'id': 'WARN-002',
            'title': 'Severe Weather Alert - North Atlantic',
            'description': 'Storm expected with wind speeds up to 30 m/s',
            'issueDate': '2024-06-02T08:00:00Z',
            'severity': 'medium',
            'location': 'North Atlantic',
            'type': 'weather'
        },
        {
            'id': 'WARN-003',
            'title': 'Navigation Notice - Strait of Malacca',
            'description': 'Temporary navigation restrictions in place',
            'issueDate': '2024-06-03T12:00:00Z',
            'severity': 'low',
            'location': 'Strait of Malacca',
            'type': 'navigation'
        }
    ]

    print(f"Mock data: {len(mock_warnings)} warnings")
    print()

    api = NGABroadcastWarnAPI()
    parsed_count = 0
    parsed_warnings = []

    for raw in mock_warnings:
        parsed = await api.parse_warning_data(raw)
        if parsed:
            parsed_warnings.append(parsed)
            parsed_count += 1
            print(f"[OK] Parsed: {parsed.title}")

    print()
    print("Analysis:")
    print("-" * 60)

    severity_count = {}
    type_count = {}
    for w in parsed_warnings:
        s = w.severity or 'unknown'
        t = w.warning_type or 'unknown'
        severity_count[s] = severity_count.get(s, 0) + 1
        type_count[t] = type_count.get(t, 0) + 1

    print(f"Severity distribution: {severity_count}")
    print(f"Type distribution: {type_count}")

    test_data = {
        'test_time': datetime.now().isoformat(),
        'test_type': 'mock',
        'total_warnings': len(mock_warnings),
        'parsed_count': parsed_count,
        'warnings': mock_warnings,
        'analysis': {
            'severity': severity_count,
            'type': type_count
        }
    }

    output_file = "tmp/nga_test_output.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(test_data, f, ensure_ascii=False, indent=2)

    print()
    print(f"[OK] Results saved to: {output_file}")

    await api.close()

    print()
    print("=" * 80)
    print("Test Complete")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
