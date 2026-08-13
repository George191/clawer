"""临时脚本：修复 satellite_today template.yaml。

根因：WP REST API 的 _embed 机制依赖 _links 来确定嵌入哪些资源。
当 _fields 排除 _links 时，_embedded 返回空数据。
之前只加了 _embedded 到 _fields，没加 _links，导致 _embedded 为空，
被误判为"API 不支持 _embed"。

修复：在 _fields 中同时加入 _embedded 和 _links，并在 list_fields 中
加入 _embedded 字段映射，让通用 assets.py 能从 wp:featuredmedia 提取封面图。
"""
import asyncio
import re
import sys

sys.path.insert(0, ".")


async def main():
    from app.storage.minio_client import get_business_metadata_minio_client
    from app.storage.postgres_client import get_pg_client

    pg = get_pg_client()
    await pg.connect()
    rows = await pg.fetch_all(
        "SELECT name, version, template FROM ai_collect_templates "
        "WHERE name LIKE '%satellite%' ORDER BY version DESC LIMIT 1"
    )
    await pg.close()

    if not rows:
        print("No satellite template found")
        return

    template_key = str(rows[0]["template"] or "")
    print(f"template_key: {template_key}")
    print(f"template name: {rows[0]['name']}, version: {rows[0]['version']}")

    minio = get_business_metadata_minio_client()
    tpl_bytes = await minio.get_object_bytes(template_key)
    if tpl_bytes is None:
        print("ERROR: template not found in MinIO")
        return

    text = tpl_bytes.decode("utf-8")
    print(f"\n{'='*60}")
    print("BEFORE (list_page + list_fields):")
    print('='*60)
    for line in text.splitlines():
        if "list_page:" in line or "list_fields:" in line or line.strip().startswith("- name:"):
            print(f"  {line}")

    modified = False

    # 1. 修改 _fields：在 featured_media 后加 ,_embedded,_links
    # 匹配 _fields=...featured_media（后面跟引号或换行或 &）
    pattern = r"(_fields=[^&\n\"']+featured_media)([&\"\n]|$)"
    match = re.search(pattern, text)
    if match:
        old = match.group(1)
        # 检查是否已经包含 _embedded 和 _links
        if "_embedded" in old and "_links" in old:
            print("\n_fields already contains _embedded and _links, skipping")
        else:
            new = old + ",_embedded,_links"
            text = text.replace(old, new)
            print(f"\nUpdated _fields: added ,_embedded,_links")
            modified = True
    else:
        print("\nWARN: could not find _fields with featured_media in URL")

    # 2. 在 list_fields 中加入 _embedded 字段映射
    embedded_field_block = """- name: _embedded
  selector: _embedded
  selector_type: json
  field_type: json
  required: false
  description: WP REST API _embed=1 嵌入资源（adapter 从 wp:featuredmedia 提取封面图）"""

    # 检查是否已存在 _embedded 字段映射
    if "name: _embedded" in text:
        print("list_fields already contains _embedded field, skipping")
    else:
        # 在 featured_media 字段映射块之后插入 _embedded 字段
        # 查找 featured_media 字段块的结尾（下一个 - name: 之前）
        # 使用更安全的方式：在 list_fields 下第一个字段之前插入
        # 找到 list_fields: 行
        lf_match = re.search(r"(list_fields:\s*\n)", text)
        if lf_match:
            insert_pos = lf_match.end()
            # 缩进 2 个空格（YAML list item）
            text = text[:insert_pos] + "  " + embedded_field_block + "\n" + text[insert_pos:]
            print("Added _embedded field to list_fields")
            modified = True
        else:
            print("WARN: could not find list_fields section")

    if not modified:
        print("\nNo changes needed. Template already up to date.")
        return

    print(f"\n{'='*60}")
    print("AFTER (list_page + list_fields):")
    print('='*60)
    for line in text.splitlines():
        if "list_page:" in line or "list_fields:" in line or line.strip().startswith("- name:"):
            print(f"  {line}")

    # 回传 MinIO
    new_bytes = text.encode("utf-8")
    await minio.upload_bytes_to_key(
        data=new_bytes,
        object_key=template_key,
        content_type="text/yaml",
    )
    print(f"\nUploaded {len(new_bytes)} chars to MinIO: {template_key}")
    print("Done. Restart crawler to load updated template.")


if __name__ == "__main__":
    asyncio.run(main())
