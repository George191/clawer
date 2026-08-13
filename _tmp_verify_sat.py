"""验证 satellite_today 的 template.yaml 和 adapter.py 配置是否正确。"""
from io import BytesIO
from minio import Minio

ENDPOINT = "192.168.20.61:9000"
ACCESS_KEY = "minioadmin"
SECRET_KEY = "minioadmin"
BUCKET = "business-metadata"
SECURE = False

client = Minio(endpoint=ENDPOINT, access_key=ACCESS_KEY, secret_key=SECRET_KEY, secure=SECURE)

# === 验证 template.yaml ===
tpl_key = "collection/templates/satellite_today/v1.0/template.yaml"
response = client.get_object(BUCKET, tpl_key)
tpl_text = response.read().decode("utf-8")
response.close()
response.release_conn()

print("="*60)
print("TEMPLATE.YAML VERIFICATION")
print("="*60)

# 检查 _fields 是否包含 _embedded 和 _links
if "_embedded,_links" in tpl_text:
    print("[OK] _fields contains _embedded,_links")
else:
    print("[FAIL] _fields missing _embedded,_links")

# 检查 list_fields 是否包含 _embedded 字段
if "name: _embedded" in tpl_text:
    print("[OK] list_fields contains _embedded field")
else:
    print("[FAIL] list_fields missing _embedded field")

# 检查 _embed=1 是否存在
if "_embed=1" in tpl_text:
    print("[OK] _embed=1 present in list_page URL")
else:
    print("[FAIL] _embed=1 missing")

# 尝试用 yaml 解析验证格式
try:
    import yaml
    data = yaml.safe_load(tpl_text)
    list_fields = data.get("list_fields", [])
    field_names = [f.get("name") for f in list_fields if isinstance(f, dict)]
    print(f"[OK] YAML parsed successfully, list_fields count={len(list_fields)}")
    print(f"     field names: {field_names}")
    # 确认 _embedded 在列表中
    if "_embedded" in field_names:
        print("[OK] _embedded is in list_fields")
    else:
        print("[FAIL] _embedded not found in parsed list_fields")
except ImportError:
    print("[SKIP] PyYAML not installed, skipping YAML parse check")
except Exception as e:
    print(f"[FAIL] YAML parse error: {e}")

# === 验证 adapter.py ===
print(f"\n{'='*60}")
print("ADAPTER.PY VERIFICATION")
print("="*60)

# 查找 adapter.py
adapter_key = None
objects = list(client.list_objects(BUCKET, prefix="collection/templates/satellite_today/", recursive=True))
for obj in objects:
    if obj.object_name.endswith("adapter.py"):
        adapter_key = obj.object_name
        print(f"Found adapter: {adapter_key}")
        break

if adapter_key:
    response = client.get_object(BUCKET, adapter_key)
    adapter_text = response.read().decode("utf-8")
    response.close()
    response.release_conn()

    # 检查是否使用通用 wp_assets.enrich_cover_images_batch
    if "wp_assets.enrich_cover_images_batch" in adapter_text:
        print("[OK] adapter uses wp_assets.enrich_cover_images_batch")
    else:
        print("[FAIL] adapter does not use wp_assets.enrich_cover_images_batch")
        # 检查是否有自定义的 _enrich_cover_images_batch
        if "_enrich_cover_images_batch" in adapter_text or "_fetch_media_url" in adapter_text:
            print("  [WARN] adapter has custom _enrich_cover_images_batch or _fetch_media_url")

    # 检查 on_after_page 方法
    if "on_after_page" in adapter_text:
        print("[OK] adapter has on_after_page method")
    else:
        print("[WARN] adapter has no on_after_page method")

    # 打印 on_after_page 相关代码
    lines = adapter_text.splitlines()
    for i, line in enumerate(lines):
        if "enrich_cover_images_batch" in line or "on_after_page" in line:
            start = max(0, i-1)
            end = min(len(lines), i+3)
            print(f"\n  Context (line {i+1}):")
            for j in range(start, end):
                print(f"    {j+1}: {lines[j]}")
else:
    print("[WARN] No adapter.py found for satellite_today")

print(f"\n{'='*60}")
print("SUMMARY")
print("="*60)
print("If all checks pass [OK], restart the crawler to load the updated template.")
print("The crawler should now use _embedded.wp:featuredmedia instead of media API.")
