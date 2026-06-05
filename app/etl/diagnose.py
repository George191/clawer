"""诊断 ETL 数据状态的脚本

用法：
    python -m app.etl.diagnose
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 确保能找到 app 包
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from app.config.settings import settings
from app.storage.postgres_client import get_pg_client


async def diagnose_data_state() -> None:
    """诊断各层数据状态"""
    print("=" * 80)
    print("ETL 数据状态诊断")
    print("=" * 80)
    
    pg = get_pg_client()
    await pg.connect()
    
    try:
        # 1. 检查 Schema 和表是否存在
        print("\n[1/7] 检查 Schema 和表是否存在...")
        try:
            # 检查所有 schema 是否存在
            schemas_result = await pg.fetch_all("""
                SELECT schema_name 
                FROM information_schema.schemata 
                WHERE schema_name IN ('ts_rds', 'ts_ods', 'ts_task', 'ts_dwd', 'ts_dws', 'ts_dim')
            """)
            print(f"  找到的 ETL schema: {[s['schema_name'] for s in schemas_result]}")

            # 检查各个表是否存在
            tables_to_check = [
                ('ts_rds', 'rds_patent'),
                ('ts_ods', 'ods_patent'),
                ('ts_task', 'task_patent'),
                ('ts_dwd', 'dwd_patent'),
            ]
            for schema_name, table_name in tables_to_check:
                table_result = await pg.fetch_one(f"""
                    SELECT COUNT(*) as cnt
                    FROM information_schema.tables
                    WHERE table_schema = '{schema_name}' AND table_name = '{table_name}'
                """)
                exists = table_result['cnt'] > 0
                print(f"  {schema_name}.{table_name}: {'exists' if exists else 'missing'}")
        except Exception as e:
            print(f"  错误: {e}")

        # 2. 检查 RDS 层数据
        print("\n[2/7] 检查 RDS 层数据...")
        try:
            rds_result = await pg.fetch_one("SELECT COUNT(*) as cnt FROM ts_rds.rds_patent")
            print(f"  ts_rds.rds_patent: {rds_result['cnt']} 条记录")
        except Exception as e:
            print(f"  错误: {e}")

        # 3. 检查 ODS 层数据
        print("\n[3/7] 检查 ODS 层数据...")
        try:
            ods_result = await pg.fetch_one("SELECT COUNT(*) as cnt FROM ts_ods.ods_patent")
            print(f"  ts_ods.ods_patent: {ods_result['cnt']} 条记录")
        except Exception as e:
            print(f"  错误: {e}")

        # 4. 检查 TASK 层数据
        print("\n[4/7] 检查 TASK 层数据...")
        try:
            task_result = await pg.fetch_one("SELECT COUNT(*) as cnt FROM ts_task.task_patent")
            print(f"  ts_task.task_patent: {task_result['cnt']} 条记录")

            task_detail = await pg.fetch_all("""
                SELECT task, COUNT(*) as task_cnt 
                FROM ts_task.task_patent 
                GROUP BY task
            """)
            for row in task_detail:
                print(f"  - {row['task']}: {row['task_cnt']} 条")
        except Exception as e:
            print(f"  错误: {e}")

        # 5. 检查 DWD 层数据
        print("\n[5/7] 检查 DWD 层数据...")
        try:
            dwd_result = await pg.fetch_one("SELECT COUNT(*) as cnt FROM ts_dwd.dwd_patent")
            print(f"  ts_dwd.dwd_patent: {dwd_result['cnt']} 条记录")

            if dwd_result['cnt'] > 0:
                dwd_detail = await pg.fetch_one("""
                    SELECT 
                        COUNT(*) as total,
                        COUNT(CASE WHEN title IS NOT NULL AND task_results IS NOT NULL THEN 1 END) as complete,
                        COUNT(CASE WHEN title IS NOT NULL AND task_results IS NULL THEN 1 END) as only_ods,
                        COUNT(CASE WHEN title IS NULL AND task_results IS NOT NULL THEN 1 END) as only_task,
                        COUNT(CASE WHEN title IS NULL AND task_results IS NULL THEN 1 END) as empty
                    FROM ts_dwd.dwd_patent
                """)
                row = dwd_detail
                print(f"  - 完整记录: {row['complete']} ({row['complete']/row['total']*100:.1f}%)")
                print(f"  - 只有 ODS: {row['only_ods']} ({row['only_ods']/row['total']*100:.1f}%)")
                print(f"  - 只有 TASK: {row['only_task']} ({row['only_task']/row['total']*100:.1f}%)")
                print(f"  - 空记录: {row['empty']}")
        except Exception as e:
            print(f"  错误: {e}")

        # 6. 检查数据一致性（按 record_id 匹配）
        print("\n[6/7] 检查各层数据一致性...")
        try:
            # 检查 ODS 和 TASK 中都有的数据量
            consistency_sql = """
                SELECT 
                    COUNT(DISTINCT o.record_id) as ods_unique,
                    COUNT(DISTINCT t.record_id) as task_unique,
                    COUNT(DISTINCT CASE WHEN o.record_id = t.record_id THEN o.record_id END) as matched
                FROM ts_ods.ods_patent o
                FULL OUTER JOIN ts_task.task_patent t 
                    ON o.record_id = t.record_id AND o.data_source = t.data_source
            """
            consistency = await pg.fetch_one(consistency_sql)
            row = consistency
            print(f"  ODS 唯一记录: {row['ods_unique']}")
            print(f"  TASK 唯一记录: {row['task_unique']}")
            print(f"  匹配记录: {row['matched']}")

            # 检查最近处理的记录
            print("\n  最近处理的记录:")
            recent_sql = """
                SELECT 
                    record_id, 
                    data_source,
                    CASE 
                        WHEN title IS NOT NULL AND task_results IS NOT NULL THEN 'COMPLETE'
                        WHEN title IS NOT NULL THEN 'ONLY_ODS'
                        WHEN task_results IS NOT NULL THEN 'ONLY_TASK'
                        ELSE 'EMPTY'
                    END as status
                FROM ts_dwd.dwd_patent 
                ORDER BY created_at DESC 
                LIMIT 10
            """
            recent = await pg.fetch_all(recent_sql)
            for i, row in enumerate(recent):
                print(f"  {i+1}. {row['record_id']} - {row['data_source']} - {row['status']}")
        except Exception as e:
            print(f"  错误: {e}")

        # 7. 检查配置
        print("\n[7/7] 检查配置...")
        print(f"  Kafka Brokers: {settings.kafka_brokers}")
        print(f"  ETL Raw Topic: {settings.etl_raw_topic}")
        print(f"  ETL RDS Topic: {settings.etl_rds_topic}")
        print(f"  ETL ODS Topic: {settings.etl_ods_topic}")
        print(f"  ETL Task Topic: {settings.etl_task_topic}")
        print(f"  ETL DWD Topic: {settings.etl_dwd_topic}")
        print(f"  ETL Graph Topic: {settings.etl_graph_topic}")
        
        print("\n" + "=" * 80)
        print("诊断完成")
        print("=" * 80)
        
    finally:
        await pg.close()


if __name__ == "__main__":
    asyncio.run(diagnose_data_state())
