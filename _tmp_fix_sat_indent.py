"""独立脚本：修复 satellite_today template.yaml 的 _embedded 字段缩进。

之前的脚本给 _embedded 项加了 2 空格前缀，导致与其他列表项缩进不一致。
"""
from io import BytesIO
from minio import Minio

ENDPOINT = "192.168.20.61:9000"
ACCESS_KEY = "minioadmin"
SECRET_KEY = "minioadmin"
BUCKET = "business-metadata"
SECURE = False
TEMPLATE_KEY = "collection/templates/satellite_today/v1.0/template.yaml"

client = Minio(endpoint=ENDPOINT, access_key=ACCESS_KEY, secret_key=SECRET_KEY, secure=SECURE)

response = client.get_object(BUCKET, TEMPLATE_KEY)
text = response.read().decode("utf-8")
response.close()
response.release_conn()

print(f"Loaded template ({len(text)} chars)")

# 查找并修正缩进：把 "  - name: _embedded" 改为 "- name: _embedded"
# 同时确保后续属性行保持 2 空格缩进（list item 属性的正确缩进）
old_block = (
    "  - name: _embedded\n"
    "  selector: _embedded\n"
    "  selector_type: json\n"
    "  field_type: json\n"
    "  required: false\n"
    '  description: WP REST API _embed=1 嵌入资源（adapter 从 wp:featuredmedia 提取封面图）'
)
new_block = (
    "- name: _embedded\n"
    "  selector: _embedded\n"
    "  selector_type: json\n"
    "  field_type: json\n"
    "  required: false\n"
    '  description: WP REST API _embed=1 嵌入资源（adapter 从 wp:featuredmedia 提取封面图）'
)

if old_block in text:
    text = text.replace(old_block, new_block)
    print("Fixed: removed 2-space indent from _embedded list item")
elif new_block in text:
    print("Already correct, no fix needed")
    raise SystemExit(0)
else:
    print("WARN: could not find the _embedded block to fix")
    # 打印 list_fields 部分帮助调试
    lines = text.splitlines()
    in_list_fields = False
    for i, line in enumerate(lines):
        if "list_fields:" in line:
            in_list_fields = True
        if in_list_fields:
            print(f"  {i+1}: {repr(line)}")
            if i > 0 and line.strip().startswith("- name:") and "embedded" not in line.lower() and in_list_fields:
                # 打印到第二个字段就停
                break
    raise SystemExit(1)

# 验证修改后的 list_fields 部分
print(f"\n{'='*60}")
print("AFTER (list_fields section):")
print('='*60)
lines = text.splitlines()
in_list_fields = False
for line in lines:
    if "list_fields:" in line:
        in_list_fields = True
    if in_list_fields:
        print(f"  {line}")
        # 打印到 list_page 或下一个顶层 section 就停
        if in_list_fields and line.strip().startswith("- name:") and "featured_media" in line:
            break

# 上传回 MinIO
new_bytes = text.encode("utf-8")
client.put_object(
    bucket_name=BUCKET,
    object_name=TEMPLATE_KEY,
    data=BytesIO(new_bytes),
    length=len(new_bytes),
    content_type="text/yaml",
)
print(f"\nUploaded {len(new_bytes)} chars to MinIO: {TEMPLATE_KEY}")
print("Done.")
