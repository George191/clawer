"""Reviewed demo import planning and durable-resume checks; no PostgreSQL calls."""

from contextlib import asynccontextmanager
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from tools import import_local_demo_object_links as importer


class Result:
    def __init__(self, value=None):
        self.value = value

    def mappings(self):
        return self

    def one_or_none(self):
        return self.value

    def first(self):
        return self.value

    def scalar_one_or_none(self):
        return self.value


def pair(number=1):
    return {"relation": "local_demo_expert_company", "relationId": "9",
            "sourceKey": {"id": f"LOCAL-EX-{number:03d}"}, "targetKey": {"id": "LOCAL-CO-001"}}


class PlanningTests(unittest.TestCase):
    def test_only_reviewed_demo_table_names_can_reach_generated_sql(self):
        self.assertEqual(importer.qualified("ts_ods", "local_demo_experts"), '"ts_ods"."local_demo_experts"')
        for schema, table in (("ts_ods", "ods_filing"), ("ts_ods", "local_demo_other"), ("public", "local_demo_experts")):
            with self.subTest(schema=schema, table=table), self.assertRaises(ValueError):
                importer.qualified(schema, table)

    def test_six_ranges_cover_every_pair_and_never_split_a_source_group(self):
        pairs = [pair(number) for number in range(100)]
        pairs.extend({**pair(number), "targetKey": {"id": "LOCAL-CO-002"}} for number in range(100))
        parts = importer.partitions({"pairs": pairs})
        self.assertEqual(len(parts), 6)
        self.assertEqual(sum(map(len, parts)), len(pairs))
        seen = set()
        for part in parts:
            source_groups = {(row["relationId"], importer.canonical_object_key(row["sourceKey"])) for row in part}
            self.assertFalse(seen & source_groups)
            seen |= source_groups
        self.assertEqual(len(seen), 100)


class ImportTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_and_removed_pairs_are_never_reactivated(self):
        for removed_at in (None, "2026-11-22T00:00:00Z"):
            calls = []

            class Session:
                async def execute(self, sql, params=None):
                    sql = str(sql); calls.append(sql)
                    if "to_regclass" in sql:
                        return Result(11 if "experts" in params["name"] else 12)
                    if "FOR KEY SHARE" in sql:
                        return Result(("LOCAL-EX-001", "LOCAL-CO-001"))
                    if "SELECT source_key" in sql:
                        return Result({"source_key": {"id": "LOCAL-EX-001"}, "target_key": {"id": "LOCAL-CO-001"}, "removed_at": removed_at})
                    return Result()

                async def scalar(self, query, params):
                    if "to_regclass" in str(query):
                        return 11 if "experts" in params["name"] else 12
                    raise AssertionError("An existing pair cannot require a new cardinality/write path")

            definition = {"name": "local_demo_expert_company"}
            expected = {definition["name"]: {"definition": definition,
                "source": {"schema": "ts_ods", "table": "local_demo_experts", "oid": 11},
                "target": {"schema": "ts_ods", "table": "local_demo_companies", "oid": 12}}}
            with patch.object(importer, "definition", AsyncMock(return_value=definition)):
                result = await importer.import_batch(Session(), expected, [pair()])
            self.assertEqual(result, {"inserted": 0, "existingOrRemoved": 1})
            self.assertTrue(all(sql.lstrip().startswith(("SELECT", "LOCK TABLE")) for sql in calls))

    async def test_resume_keeps_old_journals_and_only_reads_the_uncommitted_range(self):
        pairs = [pair(number) for number in range(3001)]
        plan = {"planId": "reviewed-plan", "relations": [{"definition": {"name": "local_demo_expert_company"}}]}

        class Pg:
            connect = AsyncMock()
            close = AsyncMock()

            @asynccontextmanager
            async def session(self):
                class Session:
                    execute = AsyncMock()
                yield Session()

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            journal = directory / "worker-0.journal"; journal.mkdir()
            original = journal / "committed.json"; original.write_text('{"next":3000}\n')
            importer.atomic_json(directory / "worker-0.checkpoint.json", {
                "planId": plan["planId"], "rangeHash": importer.fingerprint(pairs), "next": 3000,
            })
            batch = AsyncMock(return_value={"inserted": 1, "existingOrRemoved": 0})
            with patch.object(importer, "get_pg_client", return_value=Pg()), patch.object(importer, "import_batch", batch):
                result = await importer.worker_run(0, plan, pairs, directory)
            self.assertTrue(result["complete"])
            self.assertEqual(batch.await_args.args[2], pairs[3000:])
            self.assertEqual(original.read_text(), '{"next":3000}\n')
            self.assertEqual(len(list(journal.glob("*.json"))), 2)
            self.assertEqual(json.loads((directory / "worker-0.checkpoint.json").read_text())["next"], 3001)


if __name__ == "__main__":
    unittest.main()
