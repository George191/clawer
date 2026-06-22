#!/usr/bin/env python3
"""
================================================================================
MongoDB Record ID 迁移脚本
================================================================================

功能:
  1. 识别所有名称不包含 "planet" 的采集数据 MongoDB 集合
  2. 遍历上述集合中的每一条文档，获取当前的 _meta.record_id 值
  3. 复用当前项目中已有的 record_id 标准生成逻辑（md5(json.dumps(dedup_fields))），
     为每条文档生成符合规范的新 record_id
  4. 安全地更新每条文档的 _meta.record_id 字段为新生成的合法值，
     更新过程中保留文档的所有其他字段数据

安全特性:
  - --dry-run 模式：仅预览变更，不执行实际写入
  - 变更日志：详细记录每一条 record_id 的新旧值映射
  - 分批处理：避免一次性加载过大集合导致内存溢出
  - 回滚脚本：自动生成 rollback 脚本，支持一键撤销迁移
  - 幂等性：相同 dedup_fields 始终生成相同 record_id，重复执行安全

用法:
  # 预览模式（仅查看将要变更的内容，不修改数据）
  python3 scripts/migrate_record_id.py --dry-run

  # 执行迁移
  python3 scripts/migrate_record_id.py

  # 指定 MongoDB 连接
  python3 scripts/migrate_record_id.py --db-url mongodb://host:port --db-name mydb

  # 限制批量大小
  python3 scripts/migrate_record_id.py --batch-size 100

依赖:
  - pymongo（已安装 ✓）
  - pyyaml（已安装 ✓）

基于当前项目的 record_id 生成逻辑:
  app/storage/mongo_storage.py → MongoStorage._resolve_record_id()
  生成规则: md5(json.dumps(record_subset, sort_keys=True, ensure_ascii=False))
  字段子集来源: templates/<name>.yaml → dedup_fields
================================================================================
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from functools import reduce
from pathlib import Path
from typing import Any

import yaml
from pymongo import MongoClient, UpdateOne
from pymongo.errors import BulkWriteError

# ──────────────────────────────────────────────────────────────────────
#  配置
# ──────────────────────────────────────────────────────────────────────

DEFAULT_DB_URL = "mongodb://localhost:32796"
DEFAULT_DB_NAME = "raw_data"
TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "templates")

# planet 集合的排除模式（不区分大小写）
PLANET_EXCLUDE_KEYWORD = "planet"

# 回滚脚本输出路径
ROLLBACK_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "migrations")

# ──────────────────────────────────────────────────────────────────────
#  Logger
# ──────────────────────────────────────────────────────────────────────

logger = logging.getLogger("migrate_record_id")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


# ══════════════════════════════════════════════════════════════════════
#  核心函数：与项目完全一致的 record_id 生成逻辑
# ══════════════════════════════════════════════════════════════════════

def resolve_record_id(record: dict[str, Any]) -> str:
    """完全复刻 app/storage/mongo_storage.py → _resolve_record_id。

    对子集字典进行 JSON 排序序列化后做 MD5 哈希。
    """
    content = json.dumps(record, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def get_nested_value(d: dict[str, Any], path: str, default: Any = None) -> Any:
    """完全复刻 mongo_storage.py → get_nested_value。

    按 "." 分隔的路径从嵌套字典中取值。
    """
    try:
        return reduce(lambda c, k: c[k], path.split("."), d)
    except (KeyError, TypeError):
        return default


def compute_record_id(
    doc: dict[str, Any],
    dedup_fields: list[str],
) -> str:
    """根据文档和 dedup_fields 计算标准 record_id。

    与项目中 save_record 的 record_id 生成方式完全一致：
      record_id = _resolve_record_id({f: get_nested_value(record, f) for f in dedup_fields})
    """
    dedup_dict = {f: get_nested_value(doc, f) for f in dedup_fields}
    return resolve_record_id(dedup_dict)


# ══════════════════════════════════════════════════════════════════════
#  模板加载
# ══════════════════════════════════════════════════════════════════════

def load_template_dedup_fields(template_name: str) -> list[str] | None:
    """从模板 YAML 文件中加载 dedup_fields。

    Returns:
        dedup_fields 列表，若模板文件不存在或解析失败则返回 None。
    """
    template_path = os.path.join(TEMPLATE_DIR, f"{template_name}.yaml")

    if not os.path.isfile(template_path):
        logger.warning("模板文件不存在: %s", template_path)
        return None

    try:
        with open(template_path, "r", encoding="utf-8") as fh:
            template = yaml.safe_load(fh)
    except Exception:
        logger.exception("解析模板 YAML 失败: %s", template_path)
        return None

    dedup = template.get("dedup_fields", [])
    if not dedup:
        logger.warning("模板 %s 未定义 dedup_fields，跳过", template_name)
        return None

    # 确保都是字符串
    return [str(f) for f in dedup]


def get_template_from_doc(doc: dict[str, Any]) -> str | None:
    """从 MongoDB 文档的 _meta 字段中提取模板名称。"""
    meta = doc.get("_meta", {}) or {}
    return meta.get("template") or None


# ══════════════════════════════════════════════════════════════════════
#  Rollback 脚本生成
# ══════════════════════════════════════════════════════════════════════

class RollbackRecorder:
    """记录迁移变更，并生成可执行的回滚脚本。"""

    def __init__(self):
        self._changes: list[dict[str, Any]] = []

    def record(
        self,
        collection_name: str,
        doc_id: Any,
        old_record_id: str,
        new_record_id: str,
    ):
        self._changes.append({
            "collection": collection_name,
            "_id": doc_id,
            "old_record_id": old_record_id,
            "new_record_id": new_record_id,
        })

    def generate_rollback_script(self, output_path: str) -> str:
        """生成 Python 回滚脚本，返回脚本路径。"""
        script = f'''#!/usr/bin/env python3
"""回滚脚本 — 由 migrate_record_id.py 自动生成于 {datetime.now(timezone.utc).isoformat()}

将此脚本复制到 clawer/ 目录下执行:
    python3 rollback_record_id_migration.py --db-url mongodb://localhost:32796 --db-name raw_data
"""
from __future__ import annotations

import argparse
import json
import logging
from pymongo import MongoClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("rollback")

ROLLBACK_DATA = {json.dumps(self._changes, indent=2, ensure_ascii=False)}


def main():
    parser = argparse.ArgumentParser(description="回滚 record_id 迁移")
    parser.add_argument("--db-url", default="{DEFAULT_DB_URL}")
    parser.add_argument("--db-name", default="{DEFAULT_DB_NAME}")
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不执行")
    args = parser.parse_args()

    client = MongoClient(args.db_url)
    db = client[args.db_name]

    by_collection: dict[str, list] = {{}}
    for change in ROLLBACK_DATA:
        coll = change["collection"]
        if coll not in by_collection:
            by_collection[coll] = []
        by_collection[coll].append(change)

    total = 0
    for coll_name, changes in by_collection.items():
        logger.info("回滚集合 %s: %d 条记录", coll_name, len(changes))
        if args.dry_run:
            for ch in changes[:5]:
                logger.info("  [DRY RUN] %s → %s → %s", ch["_id"], ch["new_record_id"], ch["old_record_id"])
            continue

        bulk_ops = []
        for ch in changes:
            bulk_ops.append(
                UpdateOne(
                    {{"_id": ch["_id"]}},
                    {{"$set": {{"_meta.record_id": ch["old_record_id"]}}}},
                )
            )

        if bulk_ops:
            collection = db[coll_name]
            result = collection.bulk_write(bulk_ops, ordered=False)
            total += result.modified_count
            logger.info("  已回滚 %d 条", result.modified_count)

    logger.info("回滚完成: 共 %d 条记录", total)

if __name__ == "__main__":
    main()
'''
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as fh:
            fh.write(script)
        logger.info("回滚脚本已生成: %s", output_path)
        return output_path


# ══════════════════════════════════════════════════════════════════════
#  主迁移逻辑
# ══════════════════════════════════════════════════════════════════════

def should_skip_collection(name: str) -> bool:
    """判断是否应跳过该集合（名称包含 'planet'，不区分大小写）。"""
    return PLANET_EXCLUDE_KEYWORD.lower() in name.lower()


def collect_all_non_planet_collections(db) -> list[str]:
    """获取数据库中所有不包含 'planet' 的集合名称。"""
    all_collections = db.list_collection_names()
    target = [c for c in all_collections if not should_skip_collection(c)]
    skipped = [c for c in all_collections if should_skip_collection(c)]

    logger.info(
        "数据库 %s: 共 %d 个集合，目标 %d 个，跳过 %d 个 (planet)",
        db.name, len(all_collections), len(target), len(skipped),
    )
    if skipped:
        logger.info("  跳过的集合: %s", ", ".join(sorted(skipped)))
    if target:
        logger.info("  目标集合: %s", ", ".join(sorted(target)))

    return target


def preview_collections(db, collections: list[str]) -> dict[str, dict[str, Any]]:
    """预览所有目标集合的概况。

    Returns:
        {collection_name: {"total": N, "templates": {tpl: count, ...}, ...}}
    """
    preview: dict[str, dict[str, Any]] = {}
    for coll_name in collections:
        collection = db[coll_name]
        total = collection.count_documents({})
        # 统计各模板分布
        templates = defaultdict(int)
        for doc in collection.find({}, {"_meta.template": 1}).limit(1000):
            tpl = get_template_from_doc(doc) or "(无模板)"
            templates[tpl] += 1

        preview[coll_name] = {
            "total": total,
            "templates": dict(templates),
        }
    return preview


def check_dedup_field_missing(
    collection,
    dedup_fields: list[str],
    sample_size: int = 100,
) -> dict[str, int]:
    """抽样检查 dedup_fields 在文档中的缺失率。

    Returns:
        {field_name: missing_count, ...}
    """
    missing: dict[str, int] = {f: 0 for f in dedup_fields}
    count = 0
    for doc in collection.find({}, {f: 1 for f in dedup_fields}).limit(sample_size):
        count += 1
        for f in dedup_fields:
            val = get_nested_value(doc, f)
            if val is None:
                missing[f] += 1
    if count:
        for f in dedup_fields:
            missing[f] = round(missing[f] / count * 100)
    return missing


def migrate_collection(
    db,
    collection_name: str,
    rollback: RollbackRecorder,
    *,
    dry_run: bool = False,
    batch_size: int = 500,
    require_confirmation: bool = True,
) -> dict[str, Any]:
    """迁移单个集合中的所有文档。

    Returns:
        {"total": N, "updated": N, "skipped": N, "errors": N, "no_tpl": N}
    """
    collection = db[collection_name]
    total = collection.count_documents({})
    logger.info("─" * 60)
    logger.info("开始处理集合: %s（共 %d 条文档）", collection_name, total)

    # 1. 获取该集合的模板 → dedup_fields 映射
    #    同一个集合可能有多种不同模板（极端情况）
    tpl_dedup_cache: dict[str, list[str]] = {}
    tpl_dedup_cache["__MISSING__"] = []  # 哨兵值

    stats = {"total": total, "updated": 0, "skipped": 0, "errors": 0, "no_tpl": 0}
    bulk_ops: list[UpdateOne] = []

    def flush_bulk():
        nonlocal bulk_ops
        if not bulk_ops:
            return
        if dry_run:
            for op in bulk_ops[:5]:
                logger.info(
                    "  [DRY RUN] %s: record_id → %s",
                    collection_name,
                    op._doc.get("$set", {}).get("_meta.record_id", "?"),
                )
            if len(bulk_ops) > 5:
                logger.info("  ... 还有 %d 条", len(bulk_ops) - 5)
        else:
            try:
                result = collection.bulk_write(bulk_ops, ordered=False)
                stats["updated"] += result.modified_count
            except BulkWriteError as e:
                stats["errors"] += len(e.details.get("writeErrors", []))
                stats["updated"] += e.details.get("nModified", 0)
                logger.error("  BulkWrite 部分失败: %s", e.details.get("writeErrors", [])[:3])
        bulk_ops = []

    cursor = collection.find({}, batch_size=batch_size)
    processed = 0

    for doc in cursor:
        processed += 1
        if processed % max(1, total // 20) == 0 or processed == total:
            pct = processed / max(total, 1) * 100
            logger.info(
                "  进度: %d/%d (%d%%) | 已更新: %d | 跳过: %d | 错误: %d",
                processed, total, int(pct), stats["updated"], stats["skipped"], stats["errors"],
            )

        doc_id = doc["_id"]
        old_record_id = get_nested_value(doc, "_meta.record_id") or ""

        # 2. 确定模板和 dedup_fields
        template_name = get_template_from_doc(doc)

        if not template_name:
            stats["skipped"] += 1
            stats["no_tpl"] += 1
            logger.debug("  文档 %s: 无 _meta.template，跳过", doc_id)
            continue

        if template_name not in tpl_dedup_cache:
            dedup = load_template_dedup_fields(template_name)
            tpl_dedup_cache[template_name] = dedup or []

        dedup_fields = tpl_dedup_cache[template_name]

        if not dedup_fields:
            stats["skipped"] += 1
            logger.debug(
                "  文档 %s: 模板 %s 无 dedup_fields 定义，跳过",
                doc_id, template_name,
            )
            continue

        # 3. 生成新的 record_id
        new_record_id = compute_record_id(doc, dedup_fields)

        if old_record_id == new_record_id:
            stats["skipped"] += 1
            continue  # ID 未变化，无需更新

        # 4. 记录到 rollback + 加入批量更新
        rollback.record(collection_name, doc_id, old_record_id, new_record_id)
        stats["updated"] += 1

        bulk_ops.append(
            UpdateOne(
                {"_id": doc_id},
                {
                    "$set": {
                        "_meta.record_id": new_record_id,
                        "_meta.updated_at": datetime.now(timezone.utc),
                    }
                },
            )
        )

        # 5. 达到批次大小则写入
        if len(bulk_ops) >= batch_size:
            flush_bulk()

    # 6. 刷新剩余批量操作
    flush_bulk()

    logger.info(
        "集合 %s 处理完成: total=%d updated=%d skipped=%d errors=%d",
        collection_name, stats["total"], stats["updated"], stats["skipped"], stats["errors"],
    )
    return stats


def main():
    parser = argparse.ArgumentParser(
        description="MongoDB Record ID 迁移 — 按项目标准重新生成 _meta.record_id",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s --dry-run                 # 预览所有变更
  %(prog)s                           # 执行迁移
  %(prog)s --db-url mongodb://localhost:27017 --db-name mydb
  %(prog)s --collection google_patent # 仅处理指定集合
  %(prog)s --batch-size 200          # 自定义批量大小
        """,
    )
    parser.add_argument("--db-url", default=DEFAULT_DB_URL, help="MongoDB 连接字符串")
    parser.add_argument("--db-name", default=DEFAULT_DB_NAME, help="数据库名称")
    parser.add_argument("--collection", help="仅处理指定集合（可选）")
    parser.add_argument("--dry-run", action="store_true", help="预览模式，不修改数据")
    parser.add_argument("--batch-size", type=int, default=500, help="批量写入大小")
    parser.add_argument("--yes", action="store_true", help="跳过确认提示直接执行")

    args = parser.parse_args()

    # ── 连接 MongoDB ──
    logger.info("连接 MongoDB: %s", args.db_url)
    try:
        client = MongoClient(args.db_url, serverSelectionTimeoutMS=10000)
        client.admin.command("ping")
    except Exception as e:
        logger.error("MongoDB 连接失败: %s", e)
        sys.exit(1)

    db = client[args.db_name]

    # ── 步骤 1: 识别目标集合 ──
    if args.collection:
        if should_skip_collection(args.collection):
            logger.warning(
                "指定的集合 '%s' 名称包含 '%s'，将被跳过。如需强制处理请修改脚本排除规则。",
                args.collection, PLANET_EXCLUDE_KEYWORD,
            )
            sys.exit(1)
        target_collections = [args.collection]
    else:
        target_collections = collect_all_non_planet_collections(db)

    if not target_collections:
        logger.info("没有需要处理的集合。")
        client.close()
        return

    # ── 步骤 2: 预览各集合概况 ──
    logger.info("")
    logger.info("═══ 集合概况预览 ═══")
    preview = preview_collections(db, target_collections)
    for coll_name, info in preview.items():
        logger.info("  [%s] 总文档数: %d", coll_name, info["total"])
        for tpl, cnt in info["templates"].items():
            logger.info("     模板 %s: %d 条", tpl, cnt)

    # ── 步骤 3: 抽样检查 dedup_fields 缺失率 ──
    logger.info("")
    logger.info("═══ dedup_fields 缺失率抽样检查 ═══")
    for coll_name in target_collections:
        collection = db[coll_name]
        # 取第一条有模板的文档
        sample = collection.find_one({"_meta.template": {"$exists": True}}, {"_meta.template": 1})
        if not sample:
            logger.info("  [%s] 无带模板的文档", coll_name)
            continue

        tpl = get_template_from_doc(sample)
        if not tpl:
            continue
        dedup = load_template_dedup_fields(tpl)
        if not dedup:
            logger.info("  [%s] 模板 %s 无 dedup_fields", coll_name, tpl)
            continue

        missing = check_dedup_field_missing(collection, dedup)
        logger.info("  [%s] dedup_fields=%s", coll_name, dedup)
        for f, pct in missing.items():
            if pct > 0:
                logger.warning("     字段 %s: %d%% 缺失!", f, pct)
            else:
                logger.info("     字段 %s: 0%% 缺失", f)

    # ── 步骤 4: 确认 ──
    if not args.yes and not args.dry_run:
        logger.info("")
        response = input("是否继续执行迁移？输入 yes 确认: ").strip().lower()
        if response != "yes":
            logger.info("已取消。")
            client.close()
            return

    # ── 步骤 5: 执行迁移 ──
    rollback = RollbackRecorder()
    all_stats: dict[str, dict[str, Any]] = {}

    for coll_name in target_collections:
        stats = migrate_collection(
            db,
            coll_name,
            rollback,
            dry_run=args.dry_run,
            batch_size=args.batch_size,
            require_confirmation=False,  # 已在上面全局确认
        )
        all_stats[coll_name] = stats

    # ── 汇总 ──
    logger.info("")
    logger.info("═══ 迁移汇总 ═══")
    grand = {"total": 0, "updated": 0, "skipped": 0, "errors": 0}
    for coll_name, s in all_stats.items():
        for k in grand:
            grand[k] += s.get(k, 0)
        logger.info(
            "  %s: total=%d updated=%d skipped=%d errors=%d",
            coll_name, s["total"], s["updated"], s["skipped"], s["errors"],
        )
    logger.info(
        "  ─── 合计: total=%d updated=%d skipped=%d errors=%d ───",
        grand["total"], grand["updated"], grand["skipped"], grand["errors"],
    )

    # ── 步骤 6: 生成回滚脚本 ──
    if not args.dry_run and grand["updated"] > 0:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        rollback_path = os.path.join(
            ROLLBACK_DIR,
            f"rollback_record_id_{timestamp}.py",
        )
        rollback.generate_rollback_script(rollback_path)
        logger.info("如需回滚，请执行: python3 %s", rollback_path)

    # ── 步骤 7: 验证 —— 随机抽查已迁移文档 ──
    if not args.dry_run and grand["updated"] > 0:
        logger.info("")
        logger.info("═══ 迁移后验证（随机抽查 5 条已变更记录） ═══")
        verify_count = 0
        for change in rollback._changes:
            if verify_count >= 5:
                break
            coll = db[change["collection"]]
            doc = coll.find_one({"_id": change["_id"]})
            if doc:
                meta = doc.get("_meta", {})
                actual = meta.get("record_id", "")
                expected = change["new_record_id"]
                status = "✓" if actual == expected else "✗ 不匹配!"
                logger.info(
                    "  %s [%s] _id=%s expected=%s actual=%s",
                    status, change["collection"], change["_id"],
                    expected[:12] + "...", actual[:12] + "...",
                )
                if actual != expected:
                    logger.error("    验证失败! 旧值=%s", change["old_record_id"][:12] + "...")
            verify_count += 1

    client.close()
    logger.info("迁移脚本执行完毕。")

    if args.dry_run:
        logger.info("（这是预览模式，未修改任何数据。去掉 --dry-run 执行实际迁移。）")


if __name__ == "__main__":
    main()
