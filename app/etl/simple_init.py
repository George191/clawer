"""简单的 Postgres Schema 初始化。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 确保能找到 app 包
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from app.config.settings import settings
from app.storage.postgres_client import get_pg_client


async def init_schema_simple() -> None:
    """初始化所有 ETL 层的 Schema（简单直接）。"""
    print("=" * 80)
    print("初始化 Postgres ETL Schema (Simple)")
    print("=" * 80)

    pg = get_pg_client()
    await pg.connect()

    try:
        # 定义所有需要执行的 DDL
        ddls = []

        # 1. 创建 Schema
        ddls.append("CREATE SCHEMA IF NOT EXISTS ts_rds;")
        ddls.append("CREATE SCHEMA IF NOT EXISTS ts_ods;")
        ddls.append("CREATE SCHEMA IF NOT EXISTS ts_task;")
        ddls.append("CREATE SCHEMA IF NOT EXISTS ts_dwd;")
        ddls.append("CREATE SCHEMA IF NOT EXISTS ts_dws;")
        ddls.append("CREATE SCHEMA IF NOT EXISTS ts_dim;")

        # 2. RDS 层
        ddls.append("""
CREATE TABLE IF NOT EXISTS ts_rds.rds_patent (
    id                  BIGSERIAL,
    data_source         VARCHAR(128)    NOT NULL,
    data_type           VARCHAR(64)     NOT NULL,
    record_id           VARCHAR(256)    NOT NULL,
    raw_data            JSONB,
    kafka_offset        BIGINT,
    kafka_partition     INTEGER,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    CONSTRAINT pk_rds_patent PRIMARY KEY (id, created_at),
    CONSTRAINT uq_rds_record UNIQUE (record_id, data_source, created_at)
) PARTITION BY RANGE (created_at);
        """)

        # 3. ODS 层
        ddls.append("""
CREATE TABLE IF NOT EXISTS ts_ods.ods_patent (
    id                  BIGSERIAL,
    data_source         VARCHAR(128)    NOT NULL,
    data_type           VARCHAR(64)     NOT NULL,
    record_id           VARCHAR(256)    NOT NULL,
    title               TEXT,
    publication_number  VARCHAR(128),
    application_number  VARCHAR(128),
    assignee            TEXT,
    inventor            TEXT,
    publication_date    DATE,
    filing_date         DATE,
    priority_date       DATE,
    grant_date          DATE,
    abstract            TEXT,
    claims              JSONB,
    legal_status        VARCHAR(64),
    ipc_classification  VARCHAR(256),
    cpc_classification  VARCHAR(256),
    patent_type         VARCHAR(64),
    original_file       TEXT,
    thumbnail           TEXT,
    figures             JSONB,
    quality_score       DOUBLE PRECISION,
    quality_flags       JSONB,
    extra_data          JSONB,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    CONSTRAINT pk_ods_patent PRIMARY KEY (id, created_at),
    CONSTRAINT uq_ods_record UNIQUE (record_id, data_source, created_at)
) PARTITION BY RANGE (created_at);
        """)

        # 4. TASK 层
        ddls.append("""
CREATE TABLE IF NOT EXISTS ts_task.task_patent (
    id                  BIGSERIAL,
    data_source         VARCHAR(128)    NOT NULL,
    data_type           VARCHAR(64)     NOT NULL,
    record_id           VARCHAR(256)    NOT NULL,
    task                VARCHAR(64)     NOT NULL,
    raw_data            JSONB,
    task_results        JSONB,
    task_status         VARCHAR(32),
    error_msg           TEXT,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    CONSTRAINT pk_task_patent PRIMARY KEY (id, created_at),
    CONSTRAINT uq_task_record UNIQUE (record_id, data_source, task, created_at)
) PARTITION BY RANGE (created_at);
        """)

        # 5. DWD 层
        ddls.append("""
CREATE TABLE IF NOT EXISTS ts_dwd.dwd_patent (
    id                  BIGSERIAL,
    data_source         VARCHAR(128)    NOT NULL,
    data_type           VARCHAR(64)     NOT NULL,
    record_id           VARCHAR(256)    NOT NULL,
    title               TEXT,
    publication_number  VARCHAR(128),
    application_number  VARCHAR(128),
    assignee            TEXT,
    inventor            TEXT,
    publication_date    DATE,
    filing_date         DATE,
    priority_date       DATE,
    grant_date          DATE,
    abstract            TEXT,
    claims              JSONB,
    legal_status        VARCHAR(64),
    ipc_classification  VARCHAR(256),
    cpc_classification  VARCHAR(256),
    patent_type         VARCHAR(64),
    original_file       TEXT,
    thumbnail           TEXT,
    figures             JSONB,
    task_results        JSONB,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    CONSTRAINT pk_dwd_patent PRIMARY KEY (id, created_at),
    CONSTRAINT uq_dwd_record UNIQUE (record_id, data_source, created_at)
) PARTITION BY RANGE (created_at);
        """)

        # 6. 创建当前月份的分区
        from datetime import datetime
        now = datetime.now()
        month_start = datetime(now.year, now.month, 1)
        if now.month == 12:
            month_end = datetime(now.year + 1, 1, 1)
        else:
            month_end = datetime(now.year, now.month + 1, 1)

        # 为每个表创建分区
        for layer in ['rds', 'ods', 'task', 'dwd']:
            table_name = f'ts_{layer}.{layer}_patent'
            partition_name = f'ts_{layer}.{layer}_patent_{month_start:%Y%m}'
            ddls.append(f"""
CREATE TABLE IF NOT EXISTS {partition_name}
PARTITION OF {table_name}
FOR VALUES FROM ('{month_start:%Y-%m-%d}') TO ('{month_end:%Y-%m-%d}');
            """)

        # 执行所有 DDL
        print(f"\n准备执行 {len(ddls)} 个 DDL 语句...")
        for i, ddl in enumerate(ddls, 1):
            try:
                await pg.execute(ddl)
                print(f"  [{i}/{len(ddls)}] OK")
            except Exception as e:
                print(f"  [{i}/{len(ddls)}] ERROR: {e}")

        print("\n" + "=" * 80)
        print("Schema 初始化完成！")
        print("=" * 80)

    finally:
        await pg.close()


if __name__ == "__main__":
    asyncio.run(init_schema_simple())
