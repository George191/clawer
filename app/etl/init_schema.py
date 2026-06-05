"""初始化 Postgres ETL Schema。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 确保能找到 app 包
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from app.config.settings import settings
from app.storage.postgres_client import get_pg_client
from app.etl.ts_rds import TsRds
from app.etl.ts_ods import TsOds
from app.etl.ts_task import TsTask
from app.etl.ts_dwd import TsDwd
from app.etl.ts_dws import TsDws
from app.etl.ts_dim import TsDim


async def init_schema() -> None:
    """初始化所有 ETL 层的 Schema。"""
    print("=" * 80)
    print("初始化 Postgres ETL Schema")
    print("=" * 80)

    pg = get_pg_client()
    await pg.connect()

    try:
        print("\n[1/6] 初始化 RDS 层 Schema...")
        rds = TsRds()
        rds._pg = pg
        await rds._on_init_schema()
        print("  ✓ RDS 层 Schema 初始化完成")

        print("\n[2/6] 初始化 ODS 层 Schema...")
        ods = TsOds()
        ods._pg = pg
        await ods._on_init_schema()
        print("  ✓ ODS 层 Schema 初始化完成")

        print("\n[3/6] 初始化 TASK 层 Schema...")
        task = TsTask()
        task._pg = pg
        await task._on_init_schema()
        print("  ✓ TASK 层 Schema 初始化完成")

        print("\n[4/6] 初始化 DWD 层 Schema...")
        dwd = TsDwd()
        dwd._pg = pg
        await dwd._on_init_schema()
        print("  ✓ DWD 层 Schema 初始化完成")

        print("\n[5/6] 初始化 DWS 层 Schema...")
        dws = TsDws()
        dws._pg = pg
        await dws._on_init_schema()
        print("  ✓ DWS 层 Schema 初始化完成")

        print("\n[6/6] 初始化 DIM 层 Schema...")
        dim = TsDim()
        dim._pg = pg
        await dim._on_init_schema()
        print("  ✓ DIM 层 Schema 初始化完成")

        print("\n" + "=" * 80)
        print("所有 ETL 层 Schema 初始化完成！")
        print("=" * 80)

    finally:
        await pg.close()


if __name__ == "__main__":
    asyncio.run(init_schema())
