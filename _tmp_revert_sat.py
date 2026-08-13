"""临时脚本：回退 satellite_today template.yaml 的 _embedded 修改（API 不支持）。"""
import asyncio
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

    minio = get_business_metadata_minio_client()
    tpl_bytes = await minio.get_object_bytes(template_key)
    if tpl_bytes is None:
        print("ERROR: template not found in MinIO")
        return

    text = tpl_bytes.decode("utf-8")
    print(f"Original: {len(text)} chars")

    # 1. 回退 _fields 中的 ,_embedded
    if "featured_media,_embedded" in text:
        text = text.replace("featured_media,_embedded", "featured_media")
        print("Reverted: removed ,_embedded from _fields")

    # 2. 回退 list_fields 中的 _embedded 字段映射
    # 删除整个 _embedded 字段块
    embedded_block = """- name: _embedded
  selector: _embedded
  selector_type: json
  field_type: json
  required: false
  description: WP REST API _embed=1 嵌入资源（adapter 从 wp:featuredmedia 提取封面图）"""

    if embedded_block in text:
        text = text.replace(embedded_block + "\n", "")
        text = text.replace(embedded_block, "")
        print("Reverted: removed _embedded field from list_fields")

    # 3. 保留 _embed=1 在 URL 中（原来就有，无害）
    # 不回退这个

    # 回传 MinIO
    new_bytes = text.encode("utf-8")
    await minio.upload_bytes_to_key(
        data=new_bytes,
        object_key=template_key,
        content_type="text/yaml",
    )
    print(f"\nUploaded {len(new_bytes)} chars to MinIO: {template_key}")
    print("Done. satellite_today will use media API (with timeout protection).")


if __name__ == "__main__":
    asyncio.run(main())
