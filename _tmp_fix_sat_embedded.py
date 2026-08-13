"""独立脚本：修复 satellite_today template.yaml（不依赖项目模块）。

根因：WP REST API 的 _embed 依赖 _links 才能返回 _embedded。
之前只加 _embedded 到 _fields 没加 _links，导致 _embedded 为空。

修复：_fields 加 ,_embedded,_links，list_fields 加 _embedded 字段映射。
"""
import re
from minio import Minio

ENDPOINT = "192.168.20.61:9000"
ACCESS_KEY = "minioadmin"
SECRET_KEY = "minioadmin"
BUCKET = "business-metadata"
SECURE = False

client = Minio(
    endpoint=ENDPOINT,
    access_key=ACCESS_KEY,
    secret_key=SECRET_KEY,
    secure=SECURE,
)

# 1. 找到 satellite_today 的 template.yaml
print(f"Listing objects in bucket '{BUCKET}' with prefix 'templates/'...")
template_key = None
objects = list(client.list_objects(BUCKET, prefix="templates/", recursive=True))
for obj in objects:
    if "satellite" in obj.object_name.lower() and obj.object_name.endswith("template.yaml"):
        print(f"  Found: {obj.object_name} ({obj.size} bytes)")
        template_key = obj.object_name

if not template_key:
    # 尝试其他前缀
    print("\nTrying prefix 'collection/'...")
    objects = list(client.list_objects(BUCKET, prefix="collection/", recursive=True))
    for obj in objects:
        if "satellite" in obj.object_name.lower() and obj.object_name.endswith("template.yaml"):
            print(f"  Found: {obj.object_name} ({obj.size} bytes)")
            template_key = obj.object_name

if not template_key:
    # 列出所有包含 satellite 的对象
    print("\nSearching all objects containing 'satellite'...")
    objects = list(client.list_objects(BUCKET, recursive=True))
    for obj in objects:
        if "satellite" in obj.object_name.lower() and ("template" in obj.object_name.lower()):
            print(f"  Found: {obj.object_name} ({obj.size} bytes)")
            template_key = obj.object_name

if not template_key:
    print("ERROR: No satellite_today template.yaml found")
    # 列出前 20 个对象帮助调试
    print("\nFirst 20 objects in bucket:")
    objects = list(client.list_objects(BUCKET, recursive=True))
    for obj in objects[:20]:
        print(f"  {obj.object_name}")
    raise SystemExit(1)

print(f"\nUsing template_key: {template_key}")

# 2. 获取当前内容
response = client.get_object(BUCKET, template_key)
text = response.read().decode("utf-8")
response.close()
response.release_conn()

print(f"\n{'='*60}")
print("BEFORE (list_page + list_fields):")
print('='*60)
for line in text.splitlines():
    if "list_page:" in line or "list_fields:" in line or line.strip().startswith("- name:"):
        print(f"  {line}")

modified = False

# 3. 修改 _fields：在 featured_media 后加 ,_embedded,_links
pattern = r"(_fields=[^&\n\"']+featured_media)([&\"\n]|$)"
match = re.search(pattern, text)
if match:
    old = match.group(1)
    if "_embedded" in old and "_links" in old:
        print("\n_fields already contains _embedded and _links, skipping")
    else:
        new = old + ",_embedded,_links"
        text = text.replace(old, new)
        print(f"\nUpdated _fields: added ,_embedded,_links")
        modified = True
else:
    print("\nWARN: could not find _fields with featured_media in URL")

# 4. 在 list_fields 中加入 _embedded 字段映射
if "name: _embedded" in text:
    print("list_fields already contains _embedded field, skipping")
else:
    embedded_block = (
        "- name: _embedded\n"
        "  selector: _embedded\n"
        "  selector_type: json\n"
        "  field_type: json\n"
        "  required: false\n"
        '  description: WP REST API _embed=1 嵌入资源（adapter 从 wp:featuredmedia 提取封面图）'
    )
    lf_match = re.search(r"(list_fields:\s*\n)", text)
    if lf_match:
        insert_pos = lf_match.end()
        text = text[:insert_pos] + "  " + embedded_block + "\n" + text[insert_pos:]
        print("Added _embedded field to list_fields")
        modified = True
    else:
        print("WARN: could not find list_fields section")

if not modified:
    print("\nNo changes needed. Template already up to date.")
    raise SystemExit(0)

print(f"\n{'='*60}")
print("AFTER (list_page + list_fields):")
print('='*60)
for line in text.splitlines():
    if "list_page:" in line or "list_fields:" in line or line.strip().startswith("- name:"):
        print(f"  {line}")

# 5. 上传回 MinIO
from io import BytesIO
new_bytes = text.encode("utf-8")
client.put_object(
    bucket_name=BUCKET,
    object_name=template_key,
    data=BytesIO(new_bytes),
    length=len(new_bytes),
    content_type="text/yaml",
)
print(f"\nUploaded {len(new_bytes)} chars to MinIO: {template_key}")
print("Done. Restart crawler to load updated template.")
