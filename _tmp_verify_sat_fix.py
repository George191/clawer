"""临时脚本：看 satellite_today API 测试 2 的完整响应。"""
import json
from curl_cffi import requests as cffi_requests

# 测试 2: _embed=1 + _fields=_embedded（之前返回 200 但结构异常）
url = (
    "https://www.satellitetoday.com/wp-json/wp/v2/posts"
    "?_embed=1&_fields=_embedded&per_page=1&page=1"
)
print(f"GET {url}\n")

resp = cffi_requests.get(
    url,
    headers={
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    },
    timeout=30,
)
print(f"status: {resp.status_code}\n")

data = resp.json()
print(f"data type: {type(data).__name__}")
if isinstance(data, list):
    print(f"data len: {len(data)}")
    if data:
        item = data[0]
        print(f"item type: {type(item).__name__}")
        print(f"item: {json.dumps(item, ensure_ascii=False, indent=2)[:1000]}")
elif isinstance(data, dict):
    print(f"keys: {sorted(data.keys())}")
    print(json.dumps(data, ensure_ascii=False, indent=2)[:1000])
else:
    print(f"data: {str(data)[:1000]}")
