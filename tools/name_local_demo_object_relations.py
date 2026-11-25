"""Give the three reviewed synthetic relationship definitions readable names."""

import argparse
import asyncio
import json
from pathlib import Path
import sys

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.storage.postgres_client import get_pg_client

NAMES = (
    ("local_demo_expert_company", "专家所属企业", "ts_ods", "local_demo_experts"),
    ("local_demo_patent_company", "申请企业", "ts_ods", "local_demo_patents"),
    ("local_demo_project_company", "承担企业", "ts_dwd", "local_demo_project_portfolio"),
)


async def run(apply):
    pg = get_pg_client()
    await pg.connect(max_retries=1)
    report = []
    try:
        async with pg.session() as session:
            if not apply:
                await session.execute(text("SET TRANSACTION READ ONLY"))
            await session.execute(text("SET LOCAL lock_timeout='2s'"))
            await session.execute(text("SET LOCAL statement_timeout='5s'"))
            for old, name, schema, table in NAMES:
                rows = (await session.execute(text("""SELECT id, name, source_schema, source_table,
                    source_fields, target_schema, target_table, target_fields, cardinality
                    FROM app.ontology_object_relations WHERE name IN (:old, :name)
                    """ + (" FOR UPDATE" if apply else "")), {"old": old, "name": name})).mappings().all()
                if len(rows) != 1:
                    raise ValueError("The reviewed synthetic relationship definition is missing or ambiguous")
                row = rows[0]
                if (row["source_schema"], row["source_table"]) != (schema, table) or \
                        (row["target_schema"], row["target_table"]) != ("ts_ods", "local_demo_companies") or \
                        list(row["source_fields"]) != ["company_id"] or list(row["target_fields"]) != ["id"] or \
                        row["cardinality"] != "many_to_one":
                    raise ValueError("The synthetic relationship differs from its reviewed contract")
                changed = row["name"] != name
                if apply and changed:
                    await session.execute(text("""UPDATE app.ontology_object_relations
                        SET name=:name, updated_at=now() WHERE id=:id"""), {"id": row["id"], "name": name})
                report.append({"relationId": str(row["id"]), "name": name, "changed": changed and apply})
        print(json.dumps({"applied": apply, "relations": report, "businessRowsChanged": 0,
                          "linkInstancesChanged": 0}, ensure_ascii=False))
    finally:
        await pg.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    asyncio.run(run(parser.parse_args().apply))
