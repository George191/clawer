"""Declared relationship storage/preview contracts. All PostgreSQL calls are local fixtures."""

import json
import importlib.util
import io
from contextlib import asynccontextmanager
from contextlib import redirect_stdout
from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.etl.schema.object_relations import (
    OBJECT_RELATIONS_DDL,
    ObjectRelationsNotInitialized,
    manage_object_relations,
    merge_object_relations,
    read_object_relations,
    relation_mapping,
    validate_definition,
)
from app.web.tests.test_etl_object_types import Result, foreign, service, summary

SEEDS = json.loads((Path(__file__).resolve().parents[2] / "etl/schema/object_relations.seed.json").read_text(encoding="utf-8"))


def logical(row):
    return {"name": row["name"], "source_schema": row["source_schema"], "source_table": row["source_table"],
            "target_schema": row["target_schema"], "target_table": row["target_table"],
            "columns": row["source_fields"], "referenced_columns": row["target_fields"],
            "relationship_kind": row["relationship_kind"], "cardinality": row["cardinality"],
            "description": row["description"], "provenance": row["provenance"], "source_kind": "logical",
            "source_data_type": "organization" if row["source_table"] == "ods_organization" else "filing",
            "target_data_type": "filing_document" if row["target_table"] == "ods_filing_document" else "filing"}


class MetadataPg:
    def __init__(self, installed=False):
        self.installed, self.records, self.calls = installed, {}, []
        self.commits, self.rollbacks = 0, 0
        self.fail_on_name = None
        self.connected, self.closed = False, False
        self.catalog = {
            ("ts_ods", "ods_organization"): {"cik", "name"},
            ("ts_ods", "ods_filing"): {"cik", "accession_number"},
            ("ts_ods", "ods_filing_document"): {"cik", "accession_number"},
            ("ts_ods", "ods_financial_fact"): {"cik", "accn"},
        }

    async def connect(self, **_kwargs):
        self.connected = True

    async def close(self):
        self.closed = True

    @asynccontextmanager
    async def session(self):
        previous = deepcopy((self.installed, self.records))
        try:
            yield self
        except Exception:
            self.installed, self.records = previous
            self.rollbacks += 1
            raise
        else:
            self.commits += 1

    async def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        self.calls.append((sql, deepcopy(params)))
        if sql.startswith("SELECT to_regclass"):
            return Result(scalar=self.installed)
        if sql.startswith("SELECT a.attname AS name FROM pg_namespace"):
            return Result([{"name": name} for name in self.catalog.get((params["schema"], params["table"]), ())])
        if sql.startswith("SELECT name, source_schema"):
            return Result(list(deepcopy(self.records).values()))
        if sql.startswith("CREATE TABLE IF NOT EXISTS public.ontology_object_relations"):
            self.installed = True
        elif sql.startswith("INSERT INTO public.ontology_object_relations"):
            if params["name"] == self.fail_on_name:
                raise RuntimeError("Fixture rejects a metadata write")
            self.records[params["name"]] = deepcopy(params)
        elif not sql.startswith(("SELECT pg_advisory_xact_lock", "SET TRANSACTION READ ONLY",
                                 "CREATE SCHEMA IF NOT EXISTS public", "CREATE INDEX IF NOT EXISTS idx_ontology_object_relations_")):
            raise AssertionError("Unexpected or non-metadata SQL: " + sql)
        return Result()


class ObjectRelationTests(unittest.IsolatedAsyncioTestCase):
    async def test_four_verified_sec_seeds_only_reference_actual_ods_fields(self):
        pg = MetadataPg()
        report = await manage_object_relations(pg, SEEDS)
        self.assertFalse(report["installed"])
        self.assertFalse(report["applied"])
        self.assertEqual(len(report["changes"]["insert"]), 4)
        self.assertEqual(pg.records, {})
        self.assertTrue(all(sql.startswith(("SELECT", "SET TRANSACTION READ ONLY")) for sql, _ in pg.calls))
        self.assertTrue(all(row["source_schema"] == row["target_schema"] == "ts_ods" for row in SEEDS))
        self.assertEqual({(row["source_table"], tuple(row["source_fields"]), row["target_table"], tuple(row["target_fields"])) for row in SEEDS}, {
            ("ods_organization", ("cik",), "ods_filing", ("cik",)),
            ("ods_organization", ("cik",), "ods_financial_fact", ("cik",)),
            ("ods_filing", ("accession_number",), "ods_filing_document", ("accession_number",)),
            ("ods_filing", ("accession_number",), "ods_financial_fact", ("accn",)),
        })

    async def test_apply_is_atomic_idempotent_and_updates_only_the_relationship_table(self):
        pg = MetadataPg()
        first = await manage_object_relations(pg, SEEDS, apply=True)
        self.assertTrue(first["installed"])
        self.assertEqual(len(pg.records), 4)
        self.assertEqual(pg.commits, 1)
        first_writes = sum(sql.startswith("INSERT") for sql, _ in pg.calls)
        second = await manage_object_relations(pg, SEEDS, apply=True)
        self.assertEqual(len(second["changes"]["unchanged"]), 4)
        self.assertEqual(sum(sql.startswith("INSERT") for sql, _ in pg.calls), first_writes)
        changed = deepcopy(SEEDS)
        changed[0]["enabled"] = False
        changed[0]["description"] = "Explicitly disabled declared relation"
        third = await manage_object_relations(pg, changed, apply=True)
        self.assertEqual(third["changes"]["update"], [SEEDS[0]["name"]])
        self.assertFalse(pg.records[SEEDS[0]["name"]]["enabled"])
        for sql, _ in pg.calls:
            if sql.startswith("INSERT"):
                self.assertTrue(sql.startswith("INSERT INTO public.ontology_object_relations"))
            self.assertNotIn("ALTER TABLE", sql)
            self.assertNotIn("REFERENCES ts_ods", sql)

    async def test_missing_table_or_field_aborts_apply_before_ddl_or_seed_and_rolls_back(self):
        for key in (("ts_ods", "ods_filing"), ("ts_ods", "ods_financial_fact")):
            pg = MetadataPg()
            del pg.catalog[key]
            with self.assertRaisesRegex(ValueError, "missing table or"):
                await manage_object_relations(pg, SEEDS, apply=True)
            self.assertFalse(pg.installed)
            self.assertEqual(pg.records, {})
            self.assertEqual(pg.rollbacks, 1)
            self.assertFalse(any(sql.startswith(("CREATE", "INSERT")) for sql, _ in pg.calls))

    async def test_invalid_identifiers_fields_cardinality_and_provenance_fail_before_database(self):
        mutations = [{"source_schema": "public"}, {"target_schema": "ts_meta"},
                     {"target_schema": "ts_meta_hist"}, {"source_table": "x; DROP TABLE x"},
                     {"source_fields": []}, {"target_fields": ["a", "b"]},
                     {"source_fields": ["cik", "cik"], "target_fields": ["a", "b"]},
                     {"cardinality": "observed"}, {"cardinality": {}}, {"provenance": ""},
                     {"enabled": "false"}]
        for mutation in mutations:
            pg = MetadataPg()
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                await manage_object_relations(pg, [{**SEEDS[0], **mutation}], apply=True)
            self.assertEqual(pg.calls, [])

    async def test_write_failure_rolls_back_table_installation_and_all_prior_seed_records(self):
        pg = MetadataPg()
        pg.fail_on_name = SEEDS[2]["name"]
        with self.assertRaisesRegex(RuntimeError, "rejects a metadata write"):
            await manage_object_relations(pg, SEEDS, apply=True)
        self.assertFalse(pg.installed)
        self.assertEqual(pg.records, {})
        self.assertEqual(pg.commits, 0)
        self.assertEqual(pg.rollbacks, 1)

    async def test_duplicate_mapping_in_reverse_or_different_column_order_is_rejected(self):
        first = {**SEEDS[0], "source_fields": ["a", "b"], "target_fields": ["x", "y"]}
        reverse = {**first, "name": "reverse", "source_table": first["target_table"],
                   "target_table": first["source_table"], "source_fields": ["y", "x"],
                   "target_fields": ["b", "a"]}
        pg = MetadataPg()
        with self.assertRaisesRegex(ValueError, "Duplicate relationship field mapping"):
            await manage_object_relations(pg, [first, reverse], apply=True)
        self.assertEqual(pg.calls, [])
        self.assertEqual(relation_mapping(logical(first)), relation_mapping(logical(reverse)))

    async def test_existing_mapping_cannot_be_duplicated_under_a_new_name(self):
        pg = MetadataPg()
        await manage_object_relations(pg, [SEEDS[0]], apply=True)
        writes = len([sql for sql, _ in pg.calls if sql.startswith("INSERT")])
        with self.assertRaisesRegex(ValueError, "already belongs"):
            await manage_object_relations(pg, [{**SEEDS[0], "name": "different_name"}], apply=True)
        self.assertEqual(len(pg.records), 1)
        self.assertEqual(len([sql for sql, _ in pg.calls if sql.startswith("INSERT")]), writes)

    async def test_preview_includes_outgoing_logical_links_without_claiming_a_physical_constraint(self):
        instance, _ = service(Result([summary("ts_ods", "ods_organization", properties_count=0)]),
                              Result([]), Result(scalar=True), Result([logical(row) for row in SEEDS[:2]]))
        result = await instance.preview("ods", "ods_organization")
        self.assertEqual(len(result["linkedObjectTypes"]), 2)
        linked = result["linkedObjectTypes"][0]
        self.assertEqual(linked["direction"], "outgoing")
        self.assertEqual(linked["dataType"], "filing")
        self.assertEqual(linked["constraints"], [])
        self.assertEqual(linked["relationships"][0]["sourceKind"], "logical")
        self.assertEqual(linked["relationships"][0]["cardinality"], "one_to_many")
        self.assertIn("Declared SEC EDGAR", linked["relationships"][0]["provenance"])

    async def test_preview_includes_incoming_logical_links_and_never_queries_business_records(self):
        instance, session = service(Result([summary("ts_ods", "ods_filing_document", properties_count=0)]),
                                    Result([]), Result(scalar=True), Result([logical(SEEDS[2])]))
        result = await instance.preview("ts_ods", "ods_filing_document")
        self.assertEqual(result["linkedObjectTypes"][0]["id"], "ts_ods.ods_filing")
        self.assertEqual(result["linkedObjectTypes"][0]["dataType"], "filing")
        relation = result["linkedObjectTypes"][0]["relationships"][0]
        self.assertEqual(relation["direction"], "incoming")
        self.assertEqual(relation["columns"], ["accession_number"])
        self.assertEqual(relation["referencedColumns"], ["accession_number"])
        self.assertFalse(any("FROM ts_ods." in sql or "JOIN ts_ods." in sql for sql, _ in session.calls))

    async def test_missing_registry_is_explicit_503_not_a_false_empty_relationship_list(self):
        instance, _ = service(Result([summary(properties_count=0)]), Result([]), Result(scalar=False))
        with self.assertRaises(HTTPException) as error:
            await instance.preview("ods", "objects")
        self.assertEqual(error.exception.status_code, 503)
        self.assertIn("not initialized", error.exception.detail)
        instance, session = service(Result(scalar=False))
        with self.assertRaises(ObjectRelationsNotInitialized):
            await read_object_relations(session, "ts_ods", "objects")

    async def test_logical_read_revalidates_tables_columns_scope_and_enabled_without_writes(self):
        instance, session = service(Result(scalar=True), Result([]))
        self.assertEqual(await read_object_relations(session, "ts_ods", "ods_filing"), [])
        sql, params = session.calls[-1]
        self.assertEqual(params, {"schema": "ts_ods", "table": "ods_filing"})
        for requirement in ("WHERE r.enabled", "NOT sc.relispartition", "NOT tc.relispartition",
                            "a.attnum > 0 AND NOT a.attisdropped", "unnest(r.source_fields)",
                            "unnest(r.target_fields)", "('ts_meta','ts_meta_hist')", "pg_inherits"):
            self.assertIn(requirement, sql)

    async def test_foreign_key_wins_over_same_reverse_mapping_and_declared_cardinality_is_reversed(self):
        fk = foreign(source_schema="ts_ods", source_table="ods_filing", target_schema="ts_ods",
                     target_table="ods_organization", columns=["cik"], referenced=["cik"])
        merged = merge_object_relations([fk, {**fk, "name": "duplicate_fk"}], [logical(SEEDS[0])])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["source_kind"], "foreign_key")
        self.assertEqual(merged[0]["cardinality"], "many_to_one")
        self.assertEqual(merged[0]["relationship_kind"], "has_filings")
        self.assertTrue(merged[0]["provenance"].startswith("pg_constraint; Declared SEC"))
        self.assertNotIn("source_kind", fk)

    async def test_physical_constraints_remain_separate_and_unknown_fk_cardinality_is_not_guessed(self):
        fk = foreign()
        instance, _ = service(Result([summary(properties_count=0)]),
                              Result([fk]), Result(scalar=True), Result([]))
        linked = (await instance.preview("ods", "objects"))["linkedObjectTypes"][0]
        self.assertEqual(linked["constraints"][0]["name"], "fk_owner")
        self.assertNotIn("sourceKind", linked["constraints"][0])
        self.assertEqual(linked["relationships"][0]["sourceKind"], "foreign_key")
        self.assertIsNone(linked["relationships"][0]["cardinality"])

    def test_storage_ddl_is_only_one_metadata_table_and_has_no_business_foreign_keys(self):
        self.assertEqual(OBJECT_RELATIONS_DDL.count("CREATE TABLE"), 1)
        self.assertIn("CREATE TABLE IF NOT EXISTS public.ontology_object_relations", OBJECT_RELATIONS_DDL)
        self.assertNotIn("REFERENCES", OBJECT_RELATIONS_DDL)
        self.assertIn("source_fields TEXT[]", OBJECT_RELATIONS_DDL)
        self.assertIn("target_fields TEXT[]", OBJECT_RELATIONS_DDL)
        self.assertIn("enabled BOOLEAN", OBJECT_RELATIONS_DDL)
        self.assertTrue(all(validate_definition(row)["cardinality"] == "one_to_many" for row in SEEDS))


class ObjectRelationCliTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[3]
        spec = importlib.util.spec_from_file_location("object_relations_tool", root / "tools/object_relations.py")
        self.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.tool)
        self.seed_file = root / "app/etl/schema/object_relations.seed.json"

    def test_cli_help_does_not_create_an_async_loop_or_connect_to_postgresql(self):
        with patch.object(self.tool, "get_pg_client") as factory, patch("sys.argv", ["object_relations.py", "--help"]), redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as result:
                self.tool.main()
        self.assertEqual(result.exception.code, 0)
        factory.assert_not_called()

    def test_cli_check_reads_seed_and_validates_without_installing_or_upserting(self):
        pg, output = MetadataPg(), io.StringIO()
        with patch.object(self.tool, "get_pg_client", return_value=pg), patch("sys.argv", ["object_relations.py", "--check", "--seed-file", str(self.seed_file)]), redirect_stdout(output):
            code = self.tool.main()
        self.assertEqual(code, 1)  # An uninstalled registry is reported, never silently created.
        report = json.loads(output.getvalue())
        self.assertFalse(report["applied"])
        self.assertEqual(report["definitions"], 4)
        self.assertEqual(pg.records, {})
        self.assertTrue(pg.connected and pg.closed)
        self.assertFalse(any(sql.startswith(("CREATE", "INSERT")) for sql, _ in pg.calls))

    def test_cli_apply_installs_and_seeds_only_metadata_and_subsequent_check_is_clean(self):
        pg, output = MetadataPg(), io.StringIO()
        with patch.object(self.tool, "get_pg_client", return_value=pg), patch("sys.argv", ["object_relations.py", "--apply", "--seed-file", str(self.seed_file)]), redirect_stdout(output):
            self.assertEqual(self.tool.main(), 0)
        self.assertEqual(len(pg.records), 4)
        output = io.StringIO()
        with patch.object(self.tool, "get_pg_client", return_value=pg), patch("sys.argv", ["object_relations.py", "--check", "--seed-file", str(self.seed_file)]), redirect_stdout(output):
            self.assertEqual(self.tool.main(), 0)
        self.assertEqual(len(json.loads(output.getvalue())["changes"]["unchanged"]), 4)
