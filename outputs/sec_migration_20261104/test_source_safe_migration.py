import copy
import gzip
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
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


class FactsConnection(Connection):
    async def fetch(self, sql, *args):
        layer = "rds" if "ts_rds" in sql else "ods"
        if "AS after_sha256" in sql:
            self.calls.append((sql, args))
            result = []
            for rid in args[0]:
                body = self.rows.get((layer, rid, args[1]))
                if body is None:
                    continue
                row = json.loads(body)
                routing = layer != "rds" or (row["raw_data"].get("data_source") == args[1]
                          and row["raw_data"].get("_meta", {}).get("template") == args[1]
                          and row["raw_data"].get("_meta", {}).get("data_source") == args[1])
                result.append({"record_id": rid, "after_sha256": migration.pg.sha(body), "routing_valid": routing})
            return result
        if "DELETE FROM " in sql and isinstance(args[0], list):
            self.calls.append((sql, args))
            if self.fail_delete:
                raise RuntimeError("injected delete failure")
            removed = []
            for rid in args[0]:
                key = layer, rid, args[1]
                if key in self.rows:
                    removed.append({"record_id": rid, "raw": self.rows.pop(key)})
            return removed
        if "CROSS JOIN LATERAL(SELECT 1 FROM" in sql:
            self.calls.append((sql, args))
            return [{"record_id": key["record_id"], "data_source": key["data_source"]}
                    for key in json.loads(args[0]) if (layer, key["record_id"], key["data_source"]) in self.rows]
        return await super().fetch(sql, *args)


class FactsDedupTests(unittest.IsolatedAsyncioTestCase):
    def row(self, *args, **kwargs):
        return SafeMigrationTests.row(self, *args, **kwargs)

    def document(self, *args, **kwargs):
        return SafeMigrationTests.document(self, *args, **kwargs)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.plan = {"kind": "facts", "collection": "financial_fact_sec_edgar", "data_type": "financial_fact",
                     "aliases": migration.SCOPES["facts"][1], "plan_sha256": "a" * 64, "mongo_uuid": "b" * 32,
                     "parts": [{"worker": 0, "lower": "0" * 24, "upper": None}]}
        self.state = {"plan_sha256": self.plan["plan_sha256"], "worker": 0,
                      "cursor": None, "processed": 0, "pending": None, "done": False}

    def rows(self):
        return [(layer, self.row(0, source, layer, source)) for layer in ("rds", "ods")
                for source in [*self.plan["aliases"], self.plan["collection"]]]

    async def apply(self, connection):
        return await migration.apply_facts_chunk(connection, self.root, self.plan, 0, self.state, [self.document(0)])

    async def test_deletes_only_manifest_old_keys_and_preserves_all_canonical_fields(self):
        connection = FactsConnection(self.rows())
        for layer in ("rds", "ods"):
            row = self.row("unrelated", self.plan["aliases"][0], layer)
            connection.rows[(layer, "unrelated", row["data_source"])] = raw(row)
        canonical = {(layer, "0", self.plan["collection"]): connection.rows[(layer, "0", self.plan["collection"])]
                     for layer in ("rds", "ods")}
        with patch.object(migration.facts, "prepare_rows", side_effect=AssertionError("must not normalize")), \
             patch.object(migration, "locked_rows", side_effect=AssertionError("must not transfer canonical bodies")):
            state = await self.apply(connection)
        self.assertEqual(state["processed"], 1)
        self.assertEqual({key: connection.rows[key] for key in canonical}, canonical)
        self.assertEqual(len(connection.rows), 4)
        self.assertFalse(any(" SET data_source=" in sql or " SET raw_data=" in sql or "INSERT INTO" in sql
                             for sql, _ in connection.calls))
        self.assertEqual(sum("AS after_sha256" in sql for sql, _ in connection.calls), 2)
        self.assertEqual(sum("CROSS JOIN LATERAL(SELECT 1 FROM" in sql for sql, _ in connection.calls), 2)
        receipt = migration.pg.read(self.root / state["last_receipt"])
        self.assertEqual(receipt["after_sha256"], {layer: {"0": migration.pg.sha(canonical[(layer, "0", self.plan["collection"])])}
                                                 for layer in ("rds", "ods")})
        archived = [json.loads(line) for line in gzip.decompress((self.root / receipt["archive"]["path"]).read_bytes()).splitlines()]
        self.assertEqual(len(archived), 4)
        self.assertEqual({row["row"]["data_source"] for row in archived}, set(self.plan["aliases"]))
        self.assertEqual(receipt["missing_insert_attempts"], {"rds": 0, "ods": 0})

    async def test_missing_canonical_or_bad_routing_rolls_back_without_deleting_old_rows(self):
        for fault in ("missing_rds", "missing_ods", "raw_source", "template", "meta_source"):
            with self.subTest(fault=fault):
                connection = FactsConnection(self.rows())
                if fault.startswith("missing"):
                    connection.rows.pop((fault.split("_")[1], "0", self.plan["collection"]))
                else:
                    key = "rds", "0", self.plan["collection"]
                    row = json.loads(connection.rows[key])
                    if fault == "raw_source":
                        row["raw_data"]["data_source"] = "wrong"
                    else:
                        row["raw_data"]["_meta"]["template" if fault == "template" else "data_source"] = "wrong"
                    connection.rows[key] = raw(row)
                original = dict(connection.rows)
                with self.assertRaisesRegex(RuntimeError, "canonical coverage is missing|canonical routing differs"):
                    await self.apply(connection)
                self.assertEqual(connection.rows, original)
                self.assertFalse(any("DELETE FROM" in sql for sql, _ in connection.calls))
                self.assertFalse((self.root / "worker-0.json").exists())

    async def test_delete_failure_rolls_back_canonical_and_old_fields(self):
        connection = FactsConnection(self.rows())
        original = dict(connection.rows)
        connection.fail_delete = True
        with self.assertRaisesRegex(RuntimeError, "injected delete failure"):
            await self.apply(connection)
        self.assertEqual(connection.rows, original)
        self.assertFalse((self.root / "worker-0.json").exists())

    async def test_lost_ack_reuses_the_original_recovery_protocol(self):
        connection = FactsConnection(self.rows())
        connection.lose_commit = True
        with self.assertRaisesRegex(OSError, "lost commit reply"):
            await self.apply(connection)
        pending = migration.pg.read(self.root / "worker-0.json")
        self.assertEqual(pending["processed"], 0)
        recovered = await migration.recover_pending(connection, self.root, self.plan, 0, pending)
        self.assertEqual(recovered["processed"], 1)
        self.assertIsNone(recovered["pending"])
        self.assertEqual(await migration.recover_pending(connection, self.root, self.plan, 0, recovered), recovered)


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

    async def test_canonical_rows_skip_update_but_keep_complete_verification(self):
        connection = Connection([(layer, self.row(number, "patent_google", layer))
                                 for layer in ("rds", "ods") for number in range(2)])
        before = dict(connection.rows)
        with patch.object(migration, "verify_chunk", AsyncMock(wraps=migration.verify_chunk)) as verify:
            state = await self.apply(connection, [self.document(number) for number in range(2)])
        self.assertEqual(state["processed"], 2)
        self.assertEqual(connection.rows, before)
        self.assertFalse(any("UPDATE " in sql for sql, _ in connection.calls))
        verify.assert_awaited_once()
        receipt = migration.pg.read(next((self.root / "transactions").glob("*.committed.json")))
        self.assertEqual(receipt["after_sha256"], {
            layer: {str(number): migration.pg.sha(before[(layer, str(number), "patent_google")])
                    for number in range(2)} for layer in ("rds", "ods")})

    async def test_mixed_canonical_rows_update_only_incorrect_routing(self):
        rows = [(layer, self.row(number, "patent_google", layer, 123456789123456789123456789))
                for layer in ("rds", "ods") for number in range(3)]
        rows[1][1]["raw_data"]["data_source"] = "google_patent"
        rows[2][1]["raw_data"]["_meta"]["source"] = "google_patent"
        connection = Connection(rows)
        before = dict(connection.rows)
        await self.apply(connection, [self.document(number) for number in range(3)])
        updates = [(sql, args) for sql, args in connection.calls
                   if "UPDATE " in sql and "SET raw_data=" in sql]
        self.assertEqual(len(updates), 1)
        self.assertEqual({row["record_id"] for row in json.loads(updates[0][1][0])}, {"1", "2"})
        self.assertEqual(connection.rows[("rds", "0", "patent_google")], before[("rds", "0", "patent_google")])
        for layer, row in rows:
            self.assertEqual(json.loads(connection.rows[(layer, row["record_id"], "patent_google")]),
                             routed(row, layer, "google_patent", "patent_google"))

    async def test_missing_canonical_cas_result_rolls_back_prior_rename(self):
        rows = [(layer, self.row(number, "google_patent" if number == 0 else "patent_google", layer))
                for layer in ("rds", "ods") for number in range(2)]
        rows[1][1]["raw_data"]["data_source"] = "google_patent"
        connection = Connection(rows)
        before = dict(connection.rows)
        fetch = connection.fetch
        attempts = []

        async def missing_cas(sql, *args):
            if "UPDATE " in sql and "SET raw_data=" in sql:
                self.assertIn(("rds", "0", "patent_google"), connection.rows)
                attempts.append(json.loads(args[0]))
                return []
            return await fetch(sql, *args)

        with patch.object(connection, "fetch", missing_cas):
            with self.assertRaisesRegex(RuntimeError, "canonical routing UPDATE afterimages differ"):
                await self.apply(connection, [self.document(number) for number in range(2)])
        self.assertEqual([[row["record_id"] for row in attempt] for attempt in attempts], [["1"]])
        self.assertEqual(connection.rows, before)
        self.assertFalse((self.root / "worker-0.json").exists())
        self.assertFalse(list((self.root / "transactions").glob("*.prepared.json")))
        self.assertFalse(list((self.root / "transactions").glob("*.committed.json")))

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

    async def test_larger_lost_ack_batches_recover_after_runtime_returns_to_300(self):
        for count in (300, 600, 1000):
            with self.subTest(rows=count):
                root = self.root / str(count)
                migration.pg.atomic(root / "plan.json", self.plan)
                plan_bytes = (root / "plan.json").read_bytes()
                migration.pg.atomic(root / "layout.json", {"workers": 6, "page_size": 3000,
                                                         "plan_sha256": self.plan["plan_sha256"], "sql_batch_size": 300})
                connection = Connection([(layer, self.row(number, "google_patent", layer))
                                         for layer in ("rds", "ods") for number in range(count)])
                connection.lose_commit = True
                with self.assertRaises(OSError):
                    await migration.apply_chunk(connection, root, self.plan, 0, self.state,
                                                [self.document(number) for number in range(count)])
                pending = migration.pg.read(root / "worker-0.json")
                prepared = root / pending["pending"]
                prepared_bytes = prepared.read_bytes()
                self.assertEqual(migration.runtime_layout(root, self.plan)["sql_batch_size"], 300)
                if count > 300:
                    with self.assertRaisesRegex(RuntimeError, "pending receipt binding differs"):
                        await migration.recover_pending(connection, root, {**self.plan, "kind": "facts"}, 0, pending)
                recovered = await migration.recover_pending(connection, root, self.plan, 0, pending)
                self.assertEqual((recovered["processed"], recovered["cursor"], recovered["pending"]),
                                 (count, str(self.document(count - 1)["_id"]), None))
                self.assertEqual(await migration.recover_pending(connection, root, self.plan, 0, recovered),
                                 recovered)
                self.assertEqual(len(list((root / "transactions").glob("*.committed.json"))), 1)
                self.assertEqual((root / "plan.json").read_bytes(), plan_bytes)
                self.assertEqual(prepared.read_bytes(), prepared_bytes)
                self.assertEqual(len(connection.rows), count * 2)

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


class FactsFullMigrationTests(unittest.IsolatedAsyncioTestCase):
    """Exercise the facts_full identity policy without touching the bounded cleanup job."""

    def setUp(self):
        SafeMigrationTests.setUp(self)
        self.plan.update(kind="facts_full", collection="financial_fact_sec_edgar", data_type="financial_fact",
                         aliases=migration.SCOPES["facts_full"][1])

    def document(self, number):
        result = SafeMigrationTests.document(self, number)
        result.update(cik="0000000001", taxonomy="us-gaap", concept="Assets", unit="USD", value=123,
                      accn="fixture", form="10-K", filed="2026-11-16")
        return result

    row = SafeMigrationTests.row
    apply = SafeMigrationTests.apply

    # The base suite's named patent cases are independently retained; facts need their own aliases and schema.
    async def test_facts_full_mixed_old_canonical_and_missing_layers_have_one_final_identity(self):
        dedicated, company = self.plan["aliases"]
        new = self.plan["collection"]
        rows = [(layer, self.row(0, dedicated, layer, "old-only")) for layer in ("rds", "ods")]
        rows += [(layer, self.row(1, source, layer, source)) for layer in ("rds", "ods")
                 for source in (dedicated, company, new)]
        rows += [(layer, self.row(2, company, layer, "company-only")) for layer in ("rds", "ods")]
        rows += [("rds", self.row(3, company, "rds", "missing-ods"))]
        connection = Connection(rows)
        docs = [self.document(number) for number in range(5)]
        state = await self.apply(connection, docs)
        self.assertEqual(state["processed"], 5)
        self.assertEqual(set(connection.rows), {(layer, str(n), new) for layer in ("rds", "ods") for n in range(5)})
        for layer in ("rds", "ods"):
            self.assertEqual(json.loads(connection.rows[(layer, "0", new)]),
                             routed(self.row(0, dedicated, layer, "old-only"), layer, dedicated, new))
            self.assertEqual(json.loads(connection.rows[(layer, "1", new)]), self.row(1, new, layer, new))
            self.assertEqual(json.loads(connection.rows[(layer, "2", new)]),
                             routed(self.row(2, company, layer, "company-only"), layer, company, new))
        self.assertEqual(json.loads(connection.rows[("rds", "3", new)])["raw_data"]["value"], "missing-ods")
        self.assertEqual(json.loads(connection.rows[("ods", "3", new)])["concept"], "Assets")
        receipt = migration.pg.read(self.root / state["last_receipt"])
        self.assertEqual(receipt["archive"]["rows"], 4)
        self.assertEqual(receipt["missing_insert_attempts"], {"rds": 1, "ods": 2})
        original = dict(connection.rows)
        await migration.apply_chunk(connection, self.root, self.plan, 0, state, docs)
        self.assertEqual(connection.rows, original)
        self.assertFalse((self.root / "baseline-ready.json").exists())

    async def test_facts_full_lost_ack_and_cross_layer_failure_preserve_rows_and_counts(self):
        old, new = self.plan["aliases"][0], self.plan["collection"]
        rows = [(layer, self.row(0, old, layer)) for layer in ("rds", "ods")]
        connection = Connection(rows)
        connection.lose_commit = True
        with self.assertRaises(OSError):
            await self.apply(connection)
        pending = migration.pg.read(self.root / "worker-0.json")
        recovered = await migration.recover_pending(connection, self.root, self.plan, 0, pending)
        self.assertEqual(recovered["processed"], 1)
        self.assertEqual(await migration.recover_pending(connection, self.root, self.plan, 0, recovered), recovered)
        self.assertEqual(set(connection.rows), {(layer, "0", new) for layer in ("rds", "ods")})
        rows += [("ods", self.row(0, new, "ods"))]
        connection = Connection(rows)
        original = dict(connection.rows)
        connection.fail_delete = True
        before = (self.root / "worker-0.json").read_bytes()
        with self.assertRaisesRegex(RuntimeError, "injected delete failure"):
            await self.apply(connection)
        self.assertEqual(connection.rows, original)
        self.assertEqual((self.root / "worker-0.json").read_bytes(), before)


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

    def test_runtime_batches_are_bound_to_the_original_immutable_plan(self):
        plan = self.plan()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            migration.pg.atomic(root / "plan.json", plan)
            original = (root / "plan.json").read_bytes()
            self.assertEqual(migration.runtime_layout(root, plan)["sql_batch_size"], 300)
            for count in (600, 1000, 3000):
                migration.pg.atomic(root / "layout.json", {"workers": 6, "page_size": 3000, "sql_batch_size": count,
                                                         "plan_sha256": plan["plan_sha256"]})
                self.assertEqual(migration.runtime_layout(root, plan)["sql_batch_size"], count)
                migration.validate_plan(plan)
            for invalid in ({"sql_batch_size": 0}, {"sql_batch_size": 3001}, {"sql_batch_size": True},
                            {"sql_batch_size": "600"}, {"workers": 8}, {"page_size": 5000},
                            {"plan_sha256": "b" * 64}):
                with self.subTest(layout=invalid):
                    migration.pg.atomic(root / "layout.json", {"workers": 6, "page_size": 3000,
                        "sql_batch_size": 600, "plan_sha256": plan["plan_sha256"], **invalid})
                    with self.assertRaisesRegex(RuntimeError, "runtime layout"):
                        migration.runtime_layout(root, plan)
            for missing in ("workers", "page_size", "sql_batch_size", "plan_sha256"):
                with self.subTest(missing=missing):
                    layout = {"workers": 6, "page_size": 3000, "sql_batch_size": 600,
                              "plan_sha256": plan["plan_sha256"]}
                    del layout[missing]
                    migration.pg.atomic(root / "layout.json", layout)
                    with self.assertRaisesRegex(RuntimeError, "runtime layout"):
                        migration.runtime_layout(root, plan)
            self.assertEqual((root / "plan.json").read_bytes(), original)

    def test_completed_bounded_facts_cleanup_cannot_use_larger_runtime_batches(self):
        plan = {**self.plan(), "kind": "facts"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(migration.runtime_layout(root, plan)["sql_batch_size"], 300)
            migration.pg.atomic(root / "layout.json", {"workers": 6, "page_size": 3000,
                "sql_batch_size": 600, "plan_sha256": plan["plan_sha256"]})
            with self.assertRaisesRegex(RuntimeError, "bounded facts cleanup"):
                migration.runtime_layout(root, plan)

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

    def full_facts_plan(self):
        return self.sign({**self.plan(), "kind": "facts_full", "collection": "financial_fact_sec_edgar",
                          "data_type": "financial_fact", "aliases": migration.SCOPES["facts_full"][1]})

    def test_full_facts_plan_is_a_fixed_full_range_without_the_burst_manifest_or_total(self):
        plan = self.full_facts_plan()
        migration.validate_plan(plan)
        self.assertIsNone(migration.manifest_records(plan, 0))
        for override in ({"manifest_path": "burst.json"}, {"manifest_sha256": "a" * 64}):
            with self.subTest(override=override), self.assertRaisesRegex(RuntimeError, "bounded cleanup manifest"):
                migration.validate_plan(self.sign({**plan, **override}))
        plan["parts"][0]["upper_inclusive"] = plan["parts"][0]["lower"]
        with self.assertRaisesRegex(RuntimeError, "ranges have a gap"):
            migration.validate_plan(self.sign(plan))

    def test_full_facts_prepare_reads_only_endpoints_and_preserves_an_existing_completed_plan(self):
        collection, database, client = MagicMock(), MagicMock(), MagicMock()
        collection.find_one.side_effect = [{"_id": ObjectId(f"{1:024x}")}, {"_id": ObjectId(f"{6000:024x}")}]
        collection.index_information.return_value = {"_meta.record_id_1": {"unique": True, "key": [("_meta.record_id", 1)]}}
        database.list_collections.return_value = [{"info": {"uuid": bytes.fromhex("a" * 32)}}]
        database.__getitem__.return_value = collection
        client.__getitem__.return_value = database
        with tempfile.TemporaryDirectory() as directory, patch.object(migration.facts, "mongo_client", return_value=client):
            root = Path(directory)
            plan = migration.prepare_plan(root, "facts_full")
            self.assertEqual((plan["kind"], plan["collection"], plan["data_type"]),
                             ("facts_full", "financial_fact_sec_edgar", "financial_fact"))
            self.assertIsNone(plan["manifest_path"])
            self.assertIsNone(plan["manifest_sha256"])
            self.assertEqual(plan["parts"][-1]["upper_inclusive"], f"{6000:024x}")
            self.assertEqual(collection.find_one.call_count, 2)
            collection.find.assert_not_called()
            collection.count_documents.assert_not_called()
            collection.estimated_document_count.assert_not_called()
            old = migration.pg.encoded({"kind": "facts", "total": 78600, "complete": True})
            (root / "plan.json").write_bytes(old)
            collection.find_one.side_effect = [{"_id": ObjectId(f"{1:024x}")}, {"_id": ObjectId(f"{6000:024x}")}]
            with self.assertRaisesRegex(RuntimeError, "immutable receipt differs"):
                migration.prepare_plan(root, "facts_full")
            self.assertEqual((root / "plan.json").read_bytes(), old)


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

    async def test_larger_batches_resume_committed_cursor_and_report_actual_layout(self):
        self.documents.extend([{"_id": ObjectId(f"{number + 1:024x}"), "_meta": {
            "record_id": str(number), "template": "patent_google", "data_source": "patent_google", "data_type": "patent"}}
            for number in range(800, 1000)])
        original = (self.root / "plan.json").read_bytes()
        for count, expected in ((600, [600, 100]), (1000, [700])):
            with self.subTest(sql_batch_size=count):
                before = {"plan_sha256": self.plan["plan_sha256"], "worker": 0, "processed": 300,
                          "cursor": str(self.documents[299]["_id"]), "pending": None, "done": False,
                          "last_error": None}
                migration.pg.atomic(self.root / "worker-0.json", before)
                migration.pg.atomic(self.root / "layout.json", {"workers": 6, "page_size": 3000, "sql_batch_size": count,
                                                              "plan_sha256": self.plan["plan_sha256"]})
                apply, read = AsyncMock(side_effect=self.apply), MagicMock(side_effect=self.read)
                with patch.object(migration.facts, "mongo_client", return_value=MagicMock()), \
                     patch.object(migration, "validate_namespace", return_value=MagicMock()), \
                     patch.object(migration.pg, "owned_connection", AsyncMock(return_value=self.connection)), \
                     patch.object(migration.patent, "read_batch", read), \
                     patch.object(migration, "apply_chunk", apply):
                    await migration.worker_run(self.root, 0)
                self.assertEqual([len(call.args[-1]) for call in apply.await_args_list], expected)
                self.assertEqual(read.call_args_list[0].args[1]["_id"]["$gt"], self.documents[299]["_id"])
                checkpoint = migration.pg.read(self.root / "worker-0.json")
                self.assertEqual((checkpoint["processed"], checkpoint["cursor"], checkpoint["done"]),
                                 (1000, str(self.documents[-1]["_id"]), True))
                self.assertEqual(migration.write_state(self.root, self.plan, False)["sql_batch_size"], count)
                self.assertEqual((self.root / "plan.json").read_bytes(), original)

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

    async def test_failed_connection_is_terminated_before_recovering_committed_pending(self):
        self.documents = self.documents[:2]
        rows = [(layer, SafeMigrationTests.row(self, number, "google_patent", layer))
                for layer in ("rds", "ods") for number in range(2)]
        failed, healthy = Connection(rows), Connection()
        for connection in (failed, healthy):
            connection.is_closed = MagicMock(return_value=False)
            connection.close = AsyncMock()
            connection.terminate = MagicMock()
        failed.lose_commit = True
        failed.close.side_effect = TimeoutError("graceful close stalled")
        failed.terminate.side_effect = lambda: setattr(failed.is_closed, "return_value", True)
        pending_path = None

        async def connect(_root, _worker):
            nonlocal pending_path
            if not failed.is_closed():
                return failed
            checkpoint = migration.pg.read(self.root / "worker-0.json")
            self.assertEqual(checkpoint["processed"], 0)
            self.assertIsNone(checkpoint["cursor"])
            pending_path = self.root / checkpoint["pending"]
            self.assertTrue(pending_path.is_file())
            self.assertFalse(list((self.root / "transactions").glob("*.committed.json")))
            healthy.rows, healthy.outcomes, healthy.txid = failed.rows, failed.outcomes, failed.txid
            return healthy

        with patch.object(migration.facts, "mongo_client", return_value=MagicMock()), \
             patch.object(migration, "validate_namespace", return_value=MagicMock()), \
             patch.object(migration.pg, "owned_connection", AsyncMock(side_effect=connect)) as owned, \
             patch.object(migration.patent, "read_batch", side_effect=self.read), \
             patch.object(migration, "apply_chunk", AsyncMock(wraps=migration.apply_chunk)) as apply, \
             patch.object(migration.asyncio, "sleep", AsyncMock()), patch("builtins.print") as log:
            await migration.worker_run(self.root, 0)
        failed.close.assert_not_awaited()
        failed.terminate.assert_called_once_with()
        healthy.close.assert_awaited_once()
        self.assertEqual(owned.await_count, 2)
        self.assertEqual(apply.await_count, 1)
        checkpoint = migration.pg.read(self.root / "worker-0.json")
        self.assertEqual(checkpoint["processed"], 2)
        self.assertEqual(checkpoint["cursor"], str(self.documents[-1]["_id"]))
        self.assertIsNone(checkpoint["pending"])
        self.assertTrue(checkpoint["done"])
        self.assertTrue(pending_path.is_file())
        self.assertEqual(len(list((self.root / "transactions").glob("*.prepared.json"))), 1)
        self.assertEqual(len(list((self.root / "transactions").glob("*.committed.json"))), 1)
        self.assertEqual(set(healthy.rows), {(layer, str(number), "patent_google")
                                           for layer in ("rds", "ods") for number in range(2)})
        event = json.loads(log.call_args.args[0])
        self.assertEqual((event["worker"], event["error"], event["attempt"], event["max_attempts"]),
                         (0, "OSError", 1, 5))

    async def test_five_sql_failures_preserve_cursor_and_raise_original_error(self):
        connections = [MagicMock() for _ in range(5)]
        for connection in connections:
            connection.is_closed.return_value = False
            connection.close = AsyncMock(side_effect=TimeoutError("graceful close stalled"))
        before = {"plan_sha256": self.plan["plan_sha256"], "worker": 0, "processed": 300,
                  "cursor": str(self.documents[299]["_id"]), "pending": None, "done": False, "last_error": None}
        migration.pg.atomic(self.root / "worker-0.json", before)
        original = migration.asyncpg.QueryCanceledError("statement timed out")
        with patch.object(migration.facts, "mongo_client", return_value=MagicMock()), \
             patch.object(migration, "validate_namespace", return_value=MagicMock()), \
             patch.object(migration.pg, "owned_connection", AsyncMock(side_effect=connections)) as owned, \
             patch.object(migration.patent, "read_batch", side_effect=self.read), \
             patch.object(migration, "apply_chunk", AsyncMock(side_effect=original)) as apply, \
             patch.object(migration.asyncio, "sleep", AsyncMock()) as sleep, patch("builtins.print") as log:
            with self.assertRaises(migration.asyncpg.QueryCanceledError) as raised:
                await migration.worker_run(self.root, 0)
        self.assertIs(raised.exception, original)
        self.assertEqual((owned.await_count, apply.await_count, sleep.await_count), (5, 5, 4))
        for connection in connections:
            connection.terminate.assert_called_once_with()
            connection.close.assert_not_awaited()
        for call in apply.await_args_list:
            self.assertEqual(call.args[4]["processed"], 300)
            self.assertEqual(call.args[5][0]["_id"], self.documents[300]["_id"])
        checkpoint = migration.pg.read(self.root / "worker-0.json")
        self.assertEqual(checkpoint, {**before, "canary_complete": False, "last_error": "QueryCanceledError"})
        events = [json.loads(call.args[0]) for call in log.call_args_list]
        self.assertEqual([event["attempt"] for event in events], [1, 2, 3, 4, 5])
        self.assertTrue(all(event["error"] == "QueryCanceledError" for event in events))

    async def test_closed_connection_rollback_error_retries_the_uncommitted_batch_once(self):
        self.documents = self.documents[:2]
        rows = [(layer, SafeMigrationTests.row(self, number, "google_patent", layer))
                for layer in ("rds", "ods") for number in range(2)]
        failed, healthy = Connection(rows), Connection()
        original_rows = dict(failed.rows)
        for connection in (failed, healthy):
            connection.is_closed = MagicMock(return_value=False)
            connection.close = AsyncMock()
            connection.terminate = MagicMock()
        timeout = TimeoutError("query timed out")
        closed = migration.asyncpg.InterfaceError("cannot perform operation: connection is closed")
        transaction = failed.transaction

        def failed_transaction():
            inner = transaction()

            class Transaction:
                async def __aenter__(self):
                    return await inner.__aenter__()

                async def __aexit__(self, kind, value, trace):
                    await inner.__aexit__(kind, value, trace)
                    if kind is not None:
                        failed.is_closed.return_value = True
                        raise closed from value

            return Transaction()

        failed.transaction = failed_transaction
        routing_rows = migration.routing_rows

        async def routing(connection, layer, *args):
            if connection is failed and layer == "ods":
                self.assertIn(("rds", "0", "patent_google"), failed.rows)
                raise timeout
            return await routing_rows(connection, layer, *args)

        async def connect(_root, _worker):
            if not failed.is_closed():
                return failed
            checkpoint = migration.pg.read(self.root / "worker-0.json")
            self.assertEqual((checkpoint["processed"], checkpoint["cursor"], checkpoint["pending"]),
                             (0, None, None))
            self.assertEqual(failed.rows, original_rows)
            healthy.rows, healthy.outcomes, healthy.txid = failed.rows, failed.outcomes, failed.txid
            return healthy

        with patch.object(migration.facts, "mongo_client", return_value=MagicMock()), \
             patch.object(migration, "validate_namespace", return_value=MagicMock()), \
             patch.object(migration.pg, "owned_connection", AsyncMock(side_effect=connect)) as owned, \
             patch.object(migration.patent, "read_batch", side_effect=self.read), \
             patch.object(migration, "routing_rows", side_effect=routing), \
             patch.object(migration, "apply_chunk", AsyncMock(wraps=migration.apply_chunk)) as apply, \
             patch.object(migration.asyncio, "sleep", AsyncMock()) as sleep, patch("builtins.print") as log:
            await migration.worker_run(self.root, 0)
        self.assertIs(closed.__cause__, timeout)
        self.assertEqual((owned.await_count, apply.await_count, sleep.await_count), (2, 2, 1))
        failed.close.assert_not_awaited()
        failed.terminate.assert_not_called()
        healthy.close.assert_awaited_once()
        checkpoint = migration.pg.read(self.root / "worker-0.json")
        self.assertEqual((checkpoint["processed"], checkpoint["cursor"], checkpoint["pending"]),
                         (2, str(self.documents[-1]["_id"]), None))
        self.assertTrue(checkpoint["done"])
        self.assertIsNone(checkpoint["last_error"])
        self.assertEqual(len(list((self.root / "transactions").glob("*.prepared.json"))), 1)
        self.assertEqual(len(list((self.root / "transactions").glob("*.committed.json"))), 1)
        self.assertEqual(set(healthy.rows), {(layer, str(number), "patent_google")
                                           for layer in ("rds", "ods") for number in range(2)})
        self.assertEqual(json.loads(log.call_args.args[0])["error"], "InterfaceError")

    async def test_interface_error_on_open_connection_is_not_retried(self):
        error = migration.asyncpg.InterfaceError("another operation is in progress")
        with patch.object(migration.facts, "mongo_client", return_value=MagicMock()), \
             patch.object(migration, "validate_namespace", return_value=MagicMock()), \
             patch.object(migration.pg, "owned_connection", AsyncMock(return_value=self.connection)) as owned, \
             patch.object(migration.patent, "read_batch", side_effect=self.read), \
             patch.object(migration, "apply_chunk", AsyncMock(side_effect=error)) as apply, \
             patch.object(migration.asyncio, "sleep", AsyncMock()) as sleep, patch("builtins.print") as log:
            with self.assertRaises(migration.asyncpg.InterfaceError) as raised:
                await migration.worker_run(self.root, 0)
        self.assertIs(raised.exception, error)
        self.assertEqual((owned.await_count, apply.await_count), (1, 1))
        sleep.assert_not_awaited()
        log.assert_not_called()
        self.connection.close.assert_awaited_once()
        checkpoint = migration.pg.read(self.root / "worker-0.json")
        self.assertEqual((checkpoint["processed"], checkpoint["cursor"], checkpoint["last_error"]),
                         (0, None, "InterfaceError"))

    async def test_five_closed_connection_failures_preserve_the_committed_cursor(self):
        connections = [MagicMock() for _ in range(5)]
        for connection in connections:
            connection.is_closed.return_value = False
            connection.close = AsyncMock()
        before = {"plan_sha256": self.plan["plan_sha256"], "worker": 0, "processed": 300,
                  "cursor": str(self.documents[299]["_id"]), "pending": None, "done": False, "last_error": None}
        migration.pg.atomic(self.root / "worker-0.json", before)
        error = migration.asyncpg.InterfaceError("cannot perform operation: connection is closed")

        async def fail(connection, *args):
            connection.is_closed.return_value = True
            raise error

        with patch.object(migration.facts, "mongo_client", return_value=MagicMock()), \
             patch.object(migration, "validate_namespace", return_value=MagicMock()), \
             patch.object(migration.pg, "owned_connection", AsyncMock(side_effect=connections)) as owned, \
             patch.object(migration.patent, "read_batch", side_effect=self.read), \
             patch.object(migration, "apply_chunk", AsyncMock(side_effect=fail)) as apply, \
             patch.object(migration.asyncio, "sleep", AsyncMock()) as sleep, patch("builtins.print") as log:
            with self.assertRaises(migration.asyncpg.InterfaceError) as raised:
                await migration.worker_run(self.root, 0)
        self.assertIs(raised.exception, error)
        self.assertEqual((owned.await_count, apply.await_count, sleep.await_count), (5, 5, 4))
        for connection in connections:
            connection.terminate.assert_not_called()
            connection.close.assert_not_awaited()
        for call in apply.await_args_list:
            self.assertEqual(call.args[4]["processed"], 300)
            self.assertEqual(call.args[5][0]["_id"], self.documents[300]["_id"])
        self.assertEqual(migration.pg.read(self.root / "worker-0.json"),
                         {**before, "canary_complete": False, "last_error": "InterfaceError"})
        self.assertEqual([json.loads(call.args[0])["attempt"] for call in log.call_args_list], [1, 2, 3, 4, 5])

    async def test_full_facts_uses_generic_safe_writer_and_resumes_the_canary_cursor(self):
        self.plan.update(kind="facts_full", collection="financial_fact_sec_edgar", data_type="financial_fact",
                         aliases=migration.SCOPES["facts_full"][1])
        self.plan = PlanValidationTests().sign(self.plan)
        migration.pg.atomic(self.root / "plan.json", self.plan)
        for doc in self.documents:
            doc["_meta"].update(template=self.plan["collection"], data_source=self.plan["collection"], data_type="financial_fact")
        original = (self.root / "plan.json").read_bytes()
        read = MagicMock(side_effect=self.read)
        apply = AsyncMock(side_effect=self.apply)
        with patch.object(migration.facts, "mongo_client", return_value=MagicMock()), \
             patch.object(migration, "validate_namespace", return_value=MagicMock()), \
             patch.object(migration.pg, "owned_connection", AsyncMock(return_value=self.connection)), \
             patch.object(migration.facts, "read_batch", read), \
             patch.object(migration, "apply_chunk", apply), \
             patch.object(migration, "apply_facts_chunk", side_effect=AssertionError("bounded cleanup is not full migration")):
            await migration.worker_run(self.root, 0, canary=True)
            self.assertEqual(migration.pg.read(self.root / "worker-0.json")["processed"], 500)
            read.reset_mock()
            await migration.worker_run(self.root, 0)
        final = migration.pg.read(self.root / "worker-0.json")
        self.assertEqual(final["processed"], 800)
        self.assertTrue(final["done"])
        self.assertEqual(read.call_args_list[0].args[1]["_id"]["$gt"], self.documents[499]["_id"])
        self.assertEqual((self.root / "plan.json").read_bytes(), original)
        for worker in range(1, 6):
            migration.pg.atomic(self.root / f"worker-{worker}.json", {"processed": 10, "done": True})
        state = migration.write_state(self.root, self.plan, False)
        self.assertEqual(state["stage"], "mongo_coverage_complete")
        self.assertFalse(state["pg_only_alias_sweep_complete"])
        self.assertFalse((self.root / "baseline-ready.json").exists())

    async def test_facts_driver_uses_only_manifest_identities_without_reading_or_normalizing_mongo(self):
        path = Path(__file__).with_name("facts-burst-manifest-20261116.json")
        manifest = migration.pg.read(path)
        self.plan.update(kind="facts", collection="financial_fact_sec_edgar", data_type="financial_fact",
                         aliases=migration.SCOPES["facts"][1], parts=manifest["parts"],
                         mongo_uuid=manifest["mongo_uuid_hex"], manifest_path=str(path),
                         manifest_sha256=migration.pg.sha(path.read_bytes()))
        self.plan = PlanValidationTests().sign(self.plan)
        migration.pg.atomic(self.root / "plan.json", self.plan)
        apply = AsyncMock(side_effect=self.apply)
        with patch.object(migration.facts, "mongo_client", return_value=MagicMock()), \
             patch.object(migration, "validate_namespace", return_value=MagicMock()) as namespace, \
             patch.object(migration.pg, "owned_connection", AsyncMock(return_value=self.connection)), \
             patch.object(migration.facts, "read_batch", side_effect=AssertionError("must not read Mongo business docs")), \
             patch.object(migration.facts, "prepare_rows", side_effect=AssertionError("must not normalize")), \
             patch.object(migration, "apply_chunk", side_effect=AssertionError("must not use generic migration")), \
             patch.object(migration, "apply_facts_chunk", apply):
            await migration.worker_run(self.root, 0, canary=True)
        state = migration.pg.read(self.root / "worker-0.json")
        self.assertEqual(state["processed"], 500)
        self.assertTrue(state["canary_complete"])
        self.assertEqual([len(call.args[-1]) for call in apply.await_args_list], [300, 200])
        self.assertTrue(all(set(doc) == {"_id", "_meta"} for call in apply.await_args_list for doc in call.args[-1]))
        namespace.assert_called_once()

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


class PatentTailTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.plan = PlanValidationTests().plan()
        migration.pg.atomic(self.root / "plan.json", self.plan)
        for worker in range(6):
            migration.pg.atomic(self.root / f"worker-{worker}.json", {
                "plan_sha256": self.plan["plan_sha256"], "worker": worker,
                "processed": 10, "done": True, "pending": None, "last_error": None,
            })

    async def prepare(self, tail):
        scope = migration.pg.read(tail / "scope.json")
        return {"workers": 6, "batch_size": 300, "guard_table": None,
                "plan_sha256": migration.pg.sha(migration.pg.encoded(scope)),
                "units": [{"unit": layer_number * 32 + ordinal, "schema": "ts_" + layer,
                           "table": layer + "_patent", "ordinal": ordinal, "old": "google_patent",
                           "new": "patent_google", "data_type": "patent"}
                          for layer_number, layer in enumerate(("rds", "ods")) for ordinal in range(32)]}

    def finish(self, args, *, incomplete=None, pending=None):
        tail = Path(args.root)
        plan = migration.asyncio.run(self.prepare(tail))
        self.assertEqual(migration.pg.read(self.root / "state.json")["stage"], "pg_alias_tail")
        for unit in plan["units"]:
            path = tail / "checkpoints" / f"unit-{unit['unit']}.json"
            if path.exists():
                self.assertTrue(migration.pg.read(path)["done"])
            migration.pg.atomic(path, {"plan_sha256": plan["plan_sha256"], "unit": unit["unit"],
                                "done": unit["unit"] != incomplete, "pending": "pending.json" if unit["unit"] == pending else None,
                                "last_error": None, "processed": 2})

    def test_full_patent_tail_reuses_pg_six_worker_runner_and_completes_all_64_units(self):
        with patch.object(migration.pg, "prepare", side_effect=self.prepare), \
             patch.object(migration.pg, "main", side_effect=self.finish) as run:
            migration.source_alias_tail(self.root, self.plan)
        run.assert_called_once()
        self.assertFalse(run.call_args.args[0].prepare)
        self.assertIsNone(run.call_args.args[0].worker)
        state = migration.pg.read(self.root / "state.json")
        self.assertEqual((state["stage"], state["tail_units_done"], state["tail_processed"]), ("complete", 64, 128))
        self.assertTrue(state["pg_only_alias_sweep_complete"])
        self.assertFalse(state["cdc_replay"])
        self.assertFalse((self.root / "baseline-ready.json").exists())
        scope = migration.pg.read(self.root / "pg-alias-tail" / "scope.json")
        self.assertIsNone(scope["guard_table"])
        self.assertEqual(scope["scopes"][0]["old"], "google_patent")

    def test_incomplete_or_pending_tail_never_declares_complete(self):
        for overrides in ({"incomplete": 63}, {"pending": 63}):
            with self.subTest(overrides=overrides), \
                 patch.object(migration.pg, "prepare", side_effect=self.prepare), \
                 patch.object(migration.pg, "main", side_effect=lambda args: self.finish(args, **overrides)):
                with self.assertRaisesRegex(RuntimeError, "tail checkpoints are incomplete"):
                    migration.source_alias_tail(self.root, self.plan)
                state = migration.pg.read(self.root / "state.json")
                self.assertEqual(state["stage"], "pg_alias_tail")
                self.assertFalse(state["pg_only_alias_sweep_complete"])
                for path in (self.root / "pg-alias-tail" / "checkpoints").glob("*.json"):
                    path.unlink()

    def test_tail_stop_returns_paused_without_requiring_all_units_done(self):
        def pause(args):
            (Path(args.root) / "STOP").touch()

        with patch.object(migration.pg, "prepare", side_effect=self.prepare), \
             patch.object(migration.pg, "main", side_effect=pause):
            migration.source_alias_tail(self.root, self.plan)
        state = migration.pg.read(self.root / "state.json")
        self.assertEqual(state["stage"], "paused")
        self.assertIsNone(state["last_error"])
        self.assertFalse(state["pg_only_alias_sweep_complete"])
        self.assertFalse(state["cdc_replay"])

    def test_failed_tail_keeps_journals_and_resumes_same_scope(self):
        def fail_after_first_unit(args):
            tail = Path(args.root)
            plan = migration.asyncio.run(self.prepare(tail))
            migration.pg.atomic(tail / "checkpoints" / "unit-0.json", {
                "plan_sha256": plan["plan_sha256"], "unit": 0, "processed": 2, "done": True})
            raise RuntimeError("tail worker failed")

        with patch.object(migration.pg, "prepare", side_effect=self.prepare), \
             patch.object(migration.pg, "main", side_effect=fail_after_first_unit):
            with self.assertRaisesRegex(RuntimeError, "tail worker failed"):
                migration.source_alias_tail(self.root, self.plan)
        scope = (self.root / "pg-alias-tail" / "scope.json").read_bytes()
        checkpoint = (self.root / "pg-alias-tail" / "checkpoints" / "unit-0.json").read_bytes()
        with patch.object(migration.pg, "prepare", side_effect=self.prepare), \
             patch.object(migration.pg, "main", side_effect=self.finish):
            migration.source_alias_tail(self.root, self.plan)
        self.assertEqual((self.root / "pg-alias-tail" / "scope.json").read_bytes(), scope)
        self.assertTrue(json.loads(checkpoint)["done"])

    def test_main_excludes_canary_facts_incomplete_and_failed_mongo(self):
        scenarios = [("patent", True, False, 0), ("facts", False, False, 0),
                     ("patent", False, True, 0), ("patent", False, False, 1),
                     ("facts_full", True, False, 0), ("facts_full", False, True, 0),
                     ("facts_full", False, False, 1)]
        for kind, canary, incomplete, exitcode in scenarios:
            with self.subTest(kind=kind, canary=canary, incomplete=incomplete, exitcode=exitcode):
                plan = copy.deepcopy(self.plan)
                if kind != "patent":
                    plan.update(kind=kind)
                migration.pg.atomic(self.root / "plan.json", plan)
                for worker in range(6):
                    migration.pg.atomic(self.root / f"worker-{worker}.json", {
                        "processed": 13100 if kind == "facts" else 10, "done": not incomplete})
                processes = [MagicMock(exitcode=exitcode, pid=n + 1) for n in range(6)]
                for process in processes:
                    process.is_alive.return_value = False
                context = SimpleNamespace(Process=MagicMock(side_effect=processes))
                with patch("sys.argv", ["source_safe_migration", "--root", str(self.root), *(["--canary"] if canary else [])]), \
                     patch.object(migration, "validate_plan"), \
                     patch.object(migration.multiprocessing, "get_context", return_value=context), \
                     patch.object(migration, "source_alias_tail") as tail:
                    if exitcode:
                        with self.assertRaisesRegex(RuntimeError, "worker failed"):
                            migration.main()
                    else:
                        migration.main()
                    tail.assert_not_called()


class FactsFullTailTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.plan = PlanValidationTests().full_facts_plan()
        migration.pg.atomic(self.root / "plan.json", self.plan)
        for worker in range(6):
            migration.pg.atomic(self.root / f"worker-{worker}.json", {
                "processed": 10, "done": True, "pending": None, "last_error": None})

    async def prepare(self, tail):
        scope = migration.pg.read(tail / "scope.json")
        units = []
        for old in self.plan["aliases"]:
            for layer in ("rds", "ods"):
                for ordinal in range(32):
                    units.append({"unit": len(units), "schema": "ts_" + layer,
                                  "table": layer + "_financial_fact", "layer": layer, "ordinal": ordinal,
                                  "old": old, "new": "financial_fact_sec_edgar", "data_type": "financial_fact"})
        return {"plan_sha256": migration.pg.sha(migration.pg.encoded(scope)), "workers": 6,
                "batch_size": 300, "guard_table": None, "units": units}

    def finish(self, args, *, pending=None):
        tail = Path(args.root)
        plan = migration.asyncio.run(self.prepare(tail))
        for unit in plan["units"]:
            path = tail / "checkpoints" / f"unit-{unit['unit']}.json"
            if path.exists():
                self.assertTrue(migration.pg.read(path)["done"])
            migration.pg.atomic(path, {"plan_sha256": plan["plan_sha256"], "unit": unit["unit"],
                "processed": 2, "done": True, "pending": "pending.json" if unit["unit"] == pending else None,
                "last_error": None})

    def test_full_facts_tail_covers_both_aliases_and_128_units_without_cdc(self):
        with patch.object(migration.pg, "prepare", side_effect=self.prepare), \
             patch.object(migration.pg, "main", side_effect=self.finish) as run:
            migration.source_alias_tail(self.root, self.plan)
            before = (self.root / "pg-alias-tail" / "scope.json").read_bytes()
            migration.source_alias_tail(self.root, self.plan)
        self.assertEqual(run.call_count, 2)
        self.assertEqual((self.root / "pg-alias-tail" / "scope.json").read_bytes(), before)
        scope = json.loads(before)
        self.assertEqual(scope["protocol"], "source_safe_facts_full_alias_tail_v1")
        self.assertEqual(scope["mongo_plan_sha256"], self.plan["plan_sha256"])
        self.assertEqual([s["old"] for s in scope["scopes"]], self.plan["aliases"])
        self.assertTrue(all(s["new"] == "financial_fact_sec_edgar" and s["data_type"] == "financial_fact"
                            and len(s["relations"]) == 2 for s in scope["scopes"]))
        state = migration.pg.read(self.root / "state.json")
        self.assertEqual((state["stage"], state["processed"], state["tail_units_done"], state["tail_processed"]),
                         ("complete", 60, 128, 256))
        self.assertTrue(state["pg_only_alias_sweep_complete"])
        self.assertFalse(state["cdc_replay"])
        self.assertFalse((self.root / "baseline-ready.json").exists())

    def test_incomplete_alias_coverage_or_pending_tail_does_not_complete(self):
        async def missing_alias(tail):
            plan = await self.prepare(tail)
            plan["units"][-1]["old"] = self.plan["aliases"][0]
            return plan

        with patch.object(migration.pg, "prepare", side_effect=missing_alias), \
             patch.object(migration.pg, "main") as run:
            with self.assertRaisesRegex(RuntimeError, "tail plan coverage differs"):
                migration.source_alias_tail(self.root, self.plan)
        run.assert_not_called()
        with patch.object(migration.pg, "prepare", side_effect=self.prepare), \
             patch.object(migration.pg, "main", side_effect=lambda args: self.finish(args, pending=127)):
            with self.assertRaisesRegex(RuntimeError, "tail checkpoints are incomplete"):
                migration.source_alias_tail(self.root, self.plan)
        self.assertFalse(migration.pg.read(self.root / "state.json")["pg_only_alias_sweep_complete"])

    def test_main_full_facts_routes_to_tail_only_after_all_mongo_ranges_succeed(self):
        processes = [MagicMock(exitcode=0, pid=n + 1) for n in range(6)]
        for process in processes:
            process.is_alive.return_value = False
        context = SimpleNamespace(Process=MagicMock(side_effect=processes))
        with patch("sys.argv", ["source_safe_migration", "--root", str(self.root)]), \
             patch.object(migration.multiprocessing, "get_context", return_value=context), \
             patch.object(migration, "source_alias_tail") as tail:
            migration.main()
        tail.assert_called_once_with(self.root.resolve(), self.plan)
        self.assertFalse((self.root / "baseline-ready.json").exists())


if __name__ == "__main__":
    unittest.main()
