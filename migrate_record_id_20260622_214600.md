# MongoDB Record ID Migration Script

## 目标
为所有名称不含 "planet" 的 MongoDB 采集集合重新生成符合项目标准的 `_meta.record_id`。

## 脚本位置
`scripts/migrate_record_id.py`

## 核心逻辑
- 完全复刻 `app/storage/mongo_storage.py` 的 `_resolve_record_id` 方法
- 从各模板 YAML 的 `dedup_fields` 读取去重字段名
- 生成规则: `md5(json.dumps({dedup_field: value, ...}, sort_keys=True, ensure_ascii=False))`

## dedup_fields 映射

| 模板 | dedup_fields |
|------|-------------|
| google_patent | patent.publication_number |
| sealagom_navwarn | message_id |
| ssc_news / ssc_press | url |
| blacksky_news / blacksky_posts / blacksky_press | id, url |
| satellite_today | id, url |
| planet | url (已排除) |

## 安全特性
1. `--dry-run` 预览模式，不修改数据
2. 自动生成回滚脚本到 `data/migrations/`
3. 分批批量写入 (默认 500 条/批)
4. 迁移后随机抽查 5 条验证
5. 幂等性：相同 dedup_fields 始终生成相同 record_id，重复执行安全

## 使用方式
```bash
# 预览
python3 scripts/migrate_record_id.py --dry-run

# 仅处理单个集合
python3 scripts/migrate_record_id.py --collection google_patent --dry-run

# 执行迁移
python3 scripts/migrate_record_id.py

# 一键回滚（迁移后自动生成）
python3 data/migrations/rollback_record_id_YYYYMMDD_HHMMSS.py
```

## 验证结果
- 语法检查通过
- record_id 生成逻辑与项目完全一致
- 幂等性通过
