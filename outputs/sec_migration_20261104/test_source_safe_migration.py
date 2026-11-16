import copy
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bson import ObjectId

from outputs.sec_migration_20261104 import source_safe_migration as migration


def raw(row):
    return json.dumps(row, sort_keys=True)


def routed(row, layer, old, new):
    result = copy.deepcopy(row)
    result["data_source"] = new
    if layer == "rds":
        result["raw_data"]["data_source"] = new
        for name in ("template", "data_source", "source"):
            if result["raw_data"].get("_meta", {}).get(name) == old:
                result["raw_data"]["_meta"][name] = new
    return result


class Connection:
    def __init__(self, rows=()):
        self.rows = {(layer, row["record_id"], row["data_source"]): raw(row) for layer, row in rows}
        self.txid, self.outcomes, self.calls = 100, {}, []
        self.lose_commit = False
        self.fail_delete = False

    def transaction(self):
        connection = self

        class Transaction:
            async def __aenter__(self):
                self.before = dict(connection.rows)
                connection.txid += 1
                self.txid = connection.txid

            async def __aexit__(self, kind, value, trace):
                if kind:
                    connection.rows = self.before
                    connection.outcomes[self.txid] = "aborted"
                else:
                    connection.outcomes[self.txid] = "committed"
                    if connection.lose_commit:
                        connection.lose_commit = False
                        raise OSError("lost commit reply")

        return Transaction()

    async def execute(self, sql, *args):
        self.calls.append((sql, args))
        if "INSERT INTO ts_" in sql:
            layer = "rds" if "INSERT INTO ts_rds" in sql else "ods"
            for row in json.loads(args[0]):
                self.rows.setdefault((layer, row["record_id"], row["data_source"]), raw(row))

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        layer = "rds" if "ts_rds" in sql else "ods"
        if "k CROSS JOIN LATERAL" in sql:
            return [{**key, "raw": self.rows[(layer, key["record_id"], key["data_source"])]}
                    for key in json.loads(args[0]) if (layer, key["record_id"], key["data_source"]) in self.rows]
        if "AS record_id," in sql and "jsonb_array_elements" in sql:
            layer = "rds" if "'{raw_data}'" in sql else "ods"
            return [{"record_id": row["record_id"], "raw": raw(routed(row, layer, args[1], args[2]))}
                    for row in json.loads(args[0])]
        if "UPDATE " in sql and "SET data_source=" in sql:
            result = []
            for row in json.loads(args[0]):
                old, new = (layer, row["record_id"], args[1]), (layer, row["record_id"], args[2])
                if self.rows.get(old) == raw(row) and new not in self.rows:
                    self.rows[new] = raw(routed(row, layer, args[1], args[2]))
                    del self.rows[old]
                    result.append({"record_id": new[1], "data_source": new[2], "raw": self.rows[new]})
            return result
        if "UPDATE " in sql and "SET raw_data=" in sql:
            result = []
            for row in json.loads(args[0]):
                key = layer, row["record_id"], args[2]
                after = raw(routed(row, layer, args[1], args[2]))
                if self.rows.get(key) == raw(row) and after != self.rows[key]:
                    self.rows[key] = after
                    result.append({"record_id": key[1], "raw": after})
            return result
        if "DELETE FROM " in sql:
            if self.fail_delete:
                raise RuntimeError("injected delete failure")
            result = []
            for row in json.loads(args[0]):
                key = layer, row["record_id"], args[1]
                if self.rows.get(key) == raw(row):
                    del self.rows[key]
                    result.append({"record_id": key[1]})
            return result
        raise AssertionError(sql)

    async def fetchval(self, sql, *args):
        if sql == "SELECT txid_current()":
            return self.txid
        if "pg_xact_status" in sql:
            return self.outcomes.get(int(args[0]))
        raise AssertionError(sql)


class SafeMigrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.plan = {"kind": "patent", "collection": "patent_google", "data_type": "patent",
                     "aliases": ["google_patent"], "plan_sha256": "a" * 64, "mongo_uuid": "b" * 32,
                     "parts": [{"worker": 0, "lower": "0" * 24, "upper": None}]}
        self.state = {"plan_sha256": self.plan["plan_sha256"], "worker": 0,
                      "cursor": None, "processed": 0, "pending": None, "done": False}

    def document(self, number):
        return {"_id": ObjectId(f"{number + 1:024x}"), "_meta": {"record_id": str(number),
                "template": self.plan["collection"], "data_source": self.plan["collection"],
                "data_type": self.plan["data_type"]}, "patent": {"title": "Mongo title"}}

    def row(self, rid, source, layer, value="retained"):
        row = {"record_id": str(rid), "data_source": source, "data_type": self.plan["data_type"],
               "created_at": "2020-01-01T00:00:00+00:00", "updated_at": "2025-01-01T00:00:00+00:00"}
        if layer == "rds":
            row.update(raw_data={"value": value, "data_source": source, "_meta": {"template": source,
                       "data_source": source, "write_token": "unchanged"}}, kafka_offset=91, kafka_partition=3, kafka_topic="original")
        else:
            row.update(title=value, application_number="preserve application", claims=[{"claim": value}], url="original-url")
        return row

    async def apply(self, connection, docs=None):
        return await migration.apply_chunk(connection, self.root, self.plan, 0, self.state, docs or [self.document(0)])

    async def test_old_only_uses_update_and_preserves_business_and_kafka(self):
        rows = [(layer, self.row(0, "google_patent", layer, 123456789123456789123456789)) for layer in ("rds", "ods")]
        connection = Connection(rows)
        with patch.object(migration.patent, "prepare_rows", side_effect=AssertionError("existing rows must not normalize")):
            state = await self.apply(connection)
        self.assertEqual(state["processed"], 1)
        for layer, row in rows:
            self.assertEqual(json.loads(connection.rows[(layer, "0", "patent_google")]), routed(row, layer, "google_patent", "patent_google"))
        self.assertFalse(any("DELETE FROM" in sql or "INSERT INTO ts_" in sql for sql, _ in connection.calls))
        self.assertFalse((self.root / "baseline-ready.json").exists())

    async def test_canonical_wins_and_conflicting_old_rows_are_archived(self):
        rows = [(layer, self.row(0, source, layer, source)) for layer in ("rds", "ods") for source in ("google_patent", "patent_google")]
        connection = Connection(rows)
        await self.apply(connection)
        self.assertEqual(len(connection.rows), 2)
        for layer in ("rds", "ods"):
            self.assertEqual(json.loads(connection.rows[(layer, "0", "patent_google")]), self.row(0, "patent_google", layer, "patent_google"))
        receipt = migration.pg.read(next((self.root / "transactions").glob("*.committed.json")))
        archive = self.root / receipt["archive"]["path"]
        self.assertEqual(migration.pg.sha(archive.read_bytes()), receipt["archive"]["sha256"])
        archived = [json.loads(line) for line in gzip.decompress(archive.read_bytes()).splitlines()]
        self.assertEqual(len(archived), 2)
        self.assertTrue(all(row["row"]["data_source"] == "google_patent" for row in archived))

    async def test_one_missing_layer_is_inserted_without_rebuilding_the_other(self):
        existing = self.row(0, "google_patent", "rds")
        connection = Connection([("rds", existing)])
        await self.apply(connection)
        self.assertEqual(json.loads(connection.rows[("rds", "0", "patent_google")])["raw_data"]["value"], "retained")
        self.assertEqual(json.loads(connection.rows[("ods", "0", "patent_google")])["title"], "Mongo title")
        inserts = [sql for sql, _ in connection.calls if "INSERT INTO ts_" in sql]
        self.assertEqual(len(inserts), 1)
        self.assertIn("ts_ods.ods_patent", inserts[0])

    async def test_mongo_only_identity_fills_both_layers_once(self):
        connection = Connection()
        state = await self.apply(connection)
        self.assertEqual(set(connection.rows), {("rds", "0", "patent_google"), ("ods", "0", "patent_google")})
        self.state = state
        await self.apply(connection)
        self.assertEqual(len([sql for sql, _ in connection.calls if "INSERT INTO ts_" in sql]), 2)

    async def test_facts_dedicated_old_source_precedes_company_alias(self):
        self.plan.update(kind="facts", collection="financial_fact_sec_edgar", data_type="financial_fact",
                         aliases=migration.SCOPES["facts"][1])
        rows = [(layer, self.row(0, source, layer, source)) for layer in ("rds", "ods") for source in self.plan["aliases"]]
        connection = Connection(rows)
        await self.apply(connection)
        for layer in ("rds", "ods"):
            row = json.loads(connection.rows[(layer, "0", self.plan["collection"])])
            self.assertEqual(row["raw_data"]["value"] if layer == "rds" else row["title"], "sec_edgar_financial_fact")

    async def test_cross_layer_failure_rolls_back_and_never_advances_checkpoint(self):
        rows = [("rds", self.row(0, "google_patent", "rds")), ("ods", self.row(0, "google_patent", "ods")),
                ("ods", self.row(0, "patent_google", "ods"))]
        connection = Connection(rows)
        original = dict(connection.rows)
        connection.fail_delete = True
        with self.assertRaisesRegex(RuntimeError, "injected delete failure"):
            await self.apply(connection)
        self.assertEqual(connection.rows, original)
        self.assertFalse((self.root / "worker-0.json").exists())
        self.assertFalse(list((self.root / "transactions").glob("*.committed.json")))

    async def test_lost_ack_recovers_commit_without_double_counting(self):
        connection = Connection([(layer, self.row(0, "google_patent", layer)) for layer in ("rds", "ods")])
        connection.lose_commit = True
        with self.assertRaises(OSError):
            await self.apply(connection)
        pending = migration.pg.read(self.root / "worker-0.json")
        self.assertEqual(pending["processed"], 0)
        recovered = await migration.recover_pending(connection, self.root, self.plan, 0, pending)
        self.assertEqual(recovered["processed"], 1)
        self.assertIsNone(recovered["pending"])
        again = await migration.recover_pending(connection, self.root, self.plan, 0, recovered)
        self.assertEqual(again["processed"], 1)

    async def test_unknown_commit_status_or_changed_afterimage_keeps_pending(self):
        for changed in (False, True):
            with self.subTest(changed=changed):
                connection = Connection([(layer, self.row(0, "google_patent", layer)) for layer in ("rds", "ods")])
                connection.lose_commit = True
                with self.assertRaises(OSError):
                    await self.apply(connection)
                pending = migration.pg.read(self.root / "worker-0.json")
                if changed:
                    connection.rows[("ods", "0", "patent_google")] = raw(self.row(0, "patent_google", "ods", "changed"))
                else:
                    connection.outcomes[connection.txid] = None
                with self.assertRaisesRegex(RuntimeError, "unknown|afterimages changed"):
                    await migration.recover_pending(connection, self.root, self.plan, 0, pending)
                self.assertEqual(migration.pg.read(self.root / "worker-0.json"), pending)


class PlanValidationTests(unittest.TestCase):
    def test_namespace_uses_index_information_without_command_kwargs(self):
        class Collection:
            def index_information(self):
                return {"_meta.record_id_1": {"unique": True, "key": [("_meta.record_id", 1)]}}

        collection = Collection()
        database = MagicMock()
        database.list_collections.return_value = [{"info": {"uuid": bytes.fromhex("a" * 32)}}]
        database.__getitem__.return_value = collection
        client = MagicMock()
        client.__getitem__.return_value = database
        self.assertIs(migration.validate_namespace(client, self.plan()), collection)

    def plan(self):
        parts = [{**p, "worker": worker} for worker, p in enumerate(migration.facts.split_ranges("000000000000000000000001", "000000000000000000000060"))]
        parts[-1]["upper_inclusive"] = "000000000000000000000060"
        return self.sign({"protocol": migration.PROTOCOL, "kind": "patent", "collection": "patent_google", "data_type": "patent",
                          "aliases": ["google_patent"], "mongo_uuid": "a" * 32, "workers": 6, "page_size": 3000,
                          "sql_batch_size": 300, "parts": parts, "manifest_path": None, "manifest_sha256": None})

    def sign(self, plan):
        plan = {k: v for k, v in plan.items() if k != "plan_sha256"}
        return {**plan, "plan_sha256": migration.pg.sha(migration.pg.encoded(plan))}

    def test_scope_and_partition_changes_are_rejected_even_when_resigned(self):
        for field, value in (("collection", "other"), ("data_type", "other"), ("aliases", ["unknown"]),
                             ("workers", 5), ("page_size", 3001), ("sql_batch_size", 301), ("mongo_uuid", "wrong")):
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                migration.validate_plan(self.sign({**self.plan(), field: value}))
        for change in ("worker", "overlap", "gap", "empty"):
            plan = self.plan()
            if change == "worker":
                plan["parts"][1]["worker"] = 0
            elif change in ("overlap", "gap"):
                plan["parts"][1]["lower"] = f"{int(plan['parts'][1]['lower'], 16) + (-1 if change == 'overlap' else 1):024x}"
            else:
                plan["parts"][0]["upper"] = plan["parts"][0]["lower"]
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                migration.validate_plan(self.sign(plan))

    def test_real_facts_manifest_has_exactly_78600_bound_records(self):
        path = Path(__file__).with_name("facts-burst-manifest-20261116.json")
        manifest = migration.pg.read(path)
        plan = {"kind": "facts", "parts": manifest["parts"], "manifest_path": str(path), "manifest_sha256": migration.pg.sha(path.read_bytes())}
        records = [row for worker in range(6) for row in migration.manifest_records(plan, worker)]
        self.assertEqual(len(records), 78600)
        self.assertEqual(len({r["record_id"] for r in records}), 78600)

    def test_checkpoint_outside_range_or_wrong_count_is_rejected(self):
        plan = self.plan()
        state = {"plan_sha256": plan["plan_sha256"], "worker": 0, "cursor": None, "processed": 0}
        migration.validate_checkpoint(state, plan, 0, None)
        for override in ({"processed": -1}, {"processed": 1}, {"cursor": "0" * 24},
                         {"cursor": "f" * 24, "processed": 1}, {"plan_sha256": "wrong"}):
            with self.subTest(override=override), self.assertRaises(RuntimeError):
                migration.validate_checkpoint({**state, **override}, plan, 0, None)


class WorkerControlTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.plan = PlanValidationTests().plan()
        self.plan["parts"] = [{"worker": worker, "lower": f"{worker * 1000 + 1:024x}",
                               "upper": f"{(worker + 1) * 1000 + 1:024x}" if worker < 5 else None}
                              for worker in range(6)]
        self.plan["parts"][-1]["upper_inclusive"] = f"{6000:024x}"
        self.plan = PlanValidationTests().sign(self.plan)
        migration.pg.atomic(self.root / "plan.json", self.plan)
        self.documents = [{"_id": ObjectId(f"{number + 1:024x}"), "_meta": {
            "record_id": str(number), "template": "patent_google", "data_source": "patent_google", "data_type": "patent"}}
            for number in range(800)]
        self.connection = MagicMock()
        self.connection.is_closed.return_value = False
        self.connection.close = AsyncMock()

    def read(self, _collection, query):
        bounds = query["_id"]
        return [doc for doc in self.documents if
                ("$gt" not in bounds or doc["_id"] > bounds["$gt"])
                and ("$gte" not in bounds or doc["_id"] >= bounds["$gte"])
                and ("$lt" not in bounds or doc["_id"] < bounds["$lt"])
                and ("$lte" not in bounds or doc["_id"] <= bounds["$lte"])][:3000]

    async def apply(self, _connection, root, plan, worker, state, docs):
        state = {**state, "processed": state["processed"] + len(docs), "cursor": str(docs[-1]["_id"])}
        migration.pg.atomic(root / f"worker-{worker}.json", state)
        return state

    async def test_canary_then_full_reuses_the_same_plan_and_committed_cursor(self):
        read = MagicMock(side_effect=self.read)
        apply = AsyncMock(side_effect=self.apply)
        original_plan = (self.root / "plan.json").read_bytes()
        with patch.object(migration.facts, "mongo_client", return_value=MagicMock()), \
             patch.object(migration, "validate_namespace", return_value=MagicMock()), \
             patch.object(migration.pg, "owned_connection", AsyncMock(return_value=self.connection)), \
             patch.object(migration.patent, "read_batch", read), \
             patch.object(migration, "apply_chunk", apply):
            await migration.worker_run(self.root, 0, canary=True)
            canary = migration.pg.read(self.root / "worker-0.json")
            self.assertEqual(canary["processed"], 500)
            self.assertEqual([len(call.args[-1]) for call in apply.await_args_list], [300, 200])
            self.assertTrue(canary["canary_complete"])
            self.assertFalse(canary["done"])
            read.reset_mock()
            await migration.worker_run(self.root, 0)
        final = migration.pg.read(self.root / "worker-0.json")
        self.assertEqual(final["processed"], 800)
        self.assertTrue(final["done"])
        self.assertEqual(read.call_args_list[0].args[1]["_id"]["$gt"], self.documents[499]["_id"])
        self.assertEqual((self.root / "plan.json").read_bytes(), original_plan)
        self.assertFalse((self.root / "baseline-ready.json").exists())

    async def test_initial_connect_and_reconnect_timeout_are_bounded(self):
        connect = AsyncMock(side_effect=TimeoutError("connect"))
        with patch.object(migration.facts, "mongo_client", return_value=MagicMock()), \
             patch.object(migration, "validate_namespace", return_value=MagicMock()), \
             patch.object(migration.pg, "owned_connection", connect), \
             patch.object(migration.asyncio, "sleep", AsyncMock()) as sleep, \
             patch.object(migration, "apply_chunk", AsyncMock()) as apply:
            with self.assertRaises(TimeoutError):
                await migration.worker_run(self.root, 0)
        self.assertEqual(connect.await_count, 5)
        self.assertEqual(sleep.await_count, 4)
        apply.assert_not_awaited()
        checkpoint = migration.pg.read(self.root / "worker-0.json")
        self.assertEqual(checkpoint["processed"], 0)
        self.assertIsNone(checkpoint["cursor"])

    async def test_final_range_stops_at_fixed_inclusive_upper_bound(self):
        self.documents = [{"_id": ObjectId(f"{number:024x}"), "_meta": {
            "record_id": str(number), "template": "patent_google", "data_source": "patent_google", "data_type": "patent"}}
            for number in range(5001, 6501)]
        read = MagicMock(side_effect=self.read)
        with patch.object(migration.facts, "mongo_client", return_value=MagicMock()), \
             patch.object(migration, "validate_namespace", return_value=MagicMock()), \
             patch.object(migration.pg, "owned_connection", AsyncMock(return_value=self.connection)), \
             patch.object(migration.patent, "read_batch", read), \
             patch.object(migration, "apply_chunk", side_effect=self.apply):
            await migration.worker_run(self.root, 5)
        state = migration.pg.read(self.root / "worker-5.json")
        self.assertEqual(state["processed"], 1000)
        self.assertEqual(state["cursor"], f"{6000:024x}")
        self.assertTrue(state["done"])
        self.assertTrue(all(call.args[1]["_id"]["$lte"] == ObjectId(f"{6000:024x}") for call in read.call_args_list))

    async def test_unknown_failure_keeps_last_committed_chunk_without_retry(self):
        calls = []

        async def failing(connection, root, plan, worker, state, docs):
            calls.append(len(docs))
            if len(calls) == 2:
                raise RuntimeError("unknown")
            return await self.apply(connection, root, plan, worker, state, docs)

        with patch.object(migration.facts, "mongo_client", return_value=MagicMock()), \
             patch.object(migration, "validate_namespace", return_value=MagicMock()), \
             patch.object(migration.pg, "owned_connection", AsyncMock(return_value=self.connection)), \
             patch.object(migration.patent, "read_batch", side_effect=self.read), \
             patch.object(migration, "apply_chunk", side_effect=failing), \
             patch.object(migration.asyncio, "sleep", AsyncMock()) as sleep:
            with self.assertRaisesRegex(RuntimeError, "unknown"):
                await migration.worker_run(self.root, 0)
        state = migration.pg.read(self.root / "worker-0.json")
        self.assertEqual((state["processed"], state["cursor"]), (300, str(self.documents[299]["_id"])))
        self.assertFalse(state["done"])
        self.assertEqual(state["last_error"], "RuntimeError")
        self.assertEqual(calls, [300, 300])
        sleep.assert_not_awaited()

    async def test_patent_eof_never_claims_pg_tail_sweep_or_cdc_release(self):
        for worker in range(6):
            migration.pg.atomic(self.root / f"worker-{worker}.json", {"processed": 10, "done": True})
        state = migration.write_state(self.root, self.plan, False)
        self.assertEqual(state["stage"], "mongo_coverage_complete")
        self.assertFalse(state["pg_only_alias_sweep_complete"])
        self.assertFalse(state["cdc_replay"])


if __name__ == "__main__":
    unittest.main()
