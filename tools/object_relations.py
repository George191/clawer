"""Check or explicitly install/seed the ETL logical relationship registry."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.etl.schema.object_relations import manage_object_relations
from app.storage.postgres_client import get_pg_client


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Read-only validation; never creates schema or data")
    mode.add_argument("--apply", action="store_true", help="Explicit transactional metadata DDL and seed upsert")
    parser.add_argument("--seed-file", type=Path, help="JSON array of verified relationship definitions")
    args = parser.parse_args()
    definitions = json.loads(args.seed_file.read_text(encoding="utf-8")) if args.seed_file else []
    if not isinstance(definitions, list):
        parser.error("The seed file must contain a JSON array")
    return asyncio.run(run(definitions, apply=args.apply))


async def run(definitions: list[dict], *, apply: bool) -> int:
    pg = get_pg_client()
    try:
        await pg.connect(max_retries=1)
        result = await manage_object_relations(pg, definitions, apply=apply)
        print(json.dumps(result))
        return 0 if result["installed"] else 1
    finally:
        await pg.close()


if __name__ == "__main__":
    raise SystemExit(main())
