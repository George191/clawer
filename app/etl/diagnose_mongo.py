"""诊断 MongoDB 数据状态的脚本。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 确保能找到 app 包
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from app.config.settings import settings
from app.base.mongo import MongoClient


async def diagnose_mongo_state() -> None:
    """诊断 MongoDB 数据状态。"""
    print("=" * 80)
    print("MongoDB 数据状态诊断")
    print("=" * 80)

    mongo = MongoClient()
    await mongo._ensure_connection()

    try:
        print("\n[1/5] 检查所有集合：")
        collections = await mongo._db.list_collection_names()
        print(f"  发现 {len(collections)} 个集合：")
        for coll in collections:
            print(f"    - {coll}")

        print("\n[2/5] 检查各集合的记录数和状态：")
        total_records = 0
        total_pending_download = 0
        total_downloaded = 0
        total_pending_sync = 0
        total_synced = 0

        for coll_name in collections:
            collection = mongo._db[coll_name]

            count_total = await collection.count_documents({})
            count_pending_download = await collection.count_documents({
                "_meta.download_status": {"$in": ["pending", "downloading"]}
            })
            count_downloaded = await collection.count_documents({
                "_meta.download_status": "downloaded"
            })
            count_pending_sync = await collection.count_documents({
                "_meta.download_status": "downloaded",
                "_meta.sync_status": {"$ne": "synced"}
            })
            count_synced = await collection.count_documents({
                "_meta.sync_status": "synced"
            })

            total_records += count_total
            total_pending_download += count_pending_download
            total_downloaded += count_downloaded
            total_pending_sync += count_pending_sync
            total_synced += count_synced

            print(f"\n  {coll_name}:")
            print(f"    总计: {count_total}")
            print(f"    待下载: {count_pending_download}")
            print(f"    已下载: {count_downloaded}")
            print(f"    待同步: {count_pending_sync}")
            print(f"    已同步: {count_synced}")

            if count_total > 0:
                print(f"\n    样本记录:")
                sample = await collection.find_one()
                if sample:
                    print(f"      record_id: {sample.get('_meta', {}).get('record_id', 'N/A')}")
                    print(f"      download_status: {sample.get('_meta', {}).get('download_status', 'N/A')}")
                    print(f"      sync_status: {sample.get('_meta', {}).get('sync_status', 'N/A')}")

        print("\n[3/5] 汇总统计：")
        print(f"  总记录数: {total_records}")
        print(f"  待下载: {total_pending_download}")
        print(f"  已下载: {total_downloaded}")
        print(f"  待同步到 Kafka: {total_pending_sync}")
        print(f"  已同步到 Kafka: {total_synced}")

        print("\n[4/5] 查找最近 10 条待同步记录：")
        pending_records = []
        for coll_name in collections:
            collection = mongo._db[coll_name]
            cursor = collection.find({
                "_meta.download_status": "downloaded",
                "$or": [
                    {"_meta.sync_status": {"$ne": "synced"}},
                    {"_meta.sync_status": {"$exists": False}}
                ]
            }).sort("_meta.updated_at", -1).limit(10)
            async for doc in cursor:
                pending_records.append((coll_name, doc))

        if pending_records:
            for i, (coll, record) in enumerate(pending_records[:10]):
                meta = record.get("_meta", {})
                print(f"  {i+1}. {coll}")
                print(f"     record_id: {meta.get('record_id', 'N/A')}")
                print(f"     created_at: {meta.get('created_at', 'N/A')}")
                print(f"     updated_at: {meta.get('updated_at', 'N/A')}")
                print(f"     download_status: {meta.get('download_status', 'N/A')}")
                print(f"     sync_status: {meta.get('sync_status', 'N/A')}")
        else:
            print("  没有待同步的记录")

        print("\n[5/5] Kafka 配置检查：")
        print(f"  Kafka Brokers: {settings.kafka_brokers}")
        print(f"  Kafka Topic: {settings.kafka_topic}")
        print(f"  ETL Raw Topic: {settings.etl_raw_topic}")

        print("\n" + "=" * 80)
        print("诊断完成")
        print("=" * 80)

    finally:
        await mongo.close()


if __name__ == "__main__":
    asyncio.run(diagnose_mongo_state())
