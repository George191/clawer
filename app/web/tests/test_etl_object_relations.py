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
from types import SimpleNamespace

from fastapi import HTTPException

from app.etl.normalizers.social_account import normalize_twitter_author
from app.etl.normalizers.social_content import normalize_twitter_content
from app.etl.normalizers.social_identity import twitter_entity_id
from app.etl.schema.catalogs import table_columns
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

ALL_SEEDS = json.loads((Path(__file__).resolve().parents[2] / "etl/schema/object_relations.seed.json").read_text(encoding="utf-8"))
SEEDS = [row for row in ALL_SEEDS if row["name"].startswith("sec_edgar_")]
SPACE_SEEDS = [row for row in ALL_SEEDS if row["name"].startswith("space_")]
SOCIAL_SEEDS = [row for row in ALL_SEEDS if row["name"].startswith("social_twitter_")]


def logical(row):
    return {"name": row["name"], "source_schema": row["source_schema"], "source_table": row["source_table"],
            "target_schema": row["target_schema"], "target_table": row["target_table"],
            "columns": row["source_fields"], "referenced_columns": row["target_fields"],
            "relationship_kind": row["relationship_kind"], "cardinality": row["cardinality"],
            "description": row["description"], "provenance": row["provenance"], "source_kind": "logical",
            "source_data_type": row["source_table"].removeprefix("ods_"),
            "target_data_type": row["target_table"].removeprefix("ods_"),
            "source_visibility": "normal", "target_visibility": "normal"}


class MetadataPg:
    def __init__(self, installed=False):
        self.installed, self.records, self.calls = installed, {}, []
        self.commits, self.rollbacks = 0, 0
        self.fail_on_name = None
        self.connected, self.closed = False, False
        self.catalog = {("ts_ods", table): set(table_columns("ods", table.removeprefix("ods_")))
                        for table in {row[side + "_table"] for row in ALL_SEEDS for side in ("source", "target")}}

    async def connect(self, **_kwargs):
        self.connected = True

    async def close(self):
        self.closed = True

    @asynccontextmanager
    async def catalog_session(self):
        yield self

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
        if sql.startswith("CREATE TABLE IF NOT EXISTS app.ontology_object_relations"):
            self.installed = True
        elif sql.startswith("INSERT INTO app.ontology_object_relations"):
            if params["name"] == self.fail_on_name:
                raise RuntimeError("Fixture rejects a metadata write")
            self.records[params["name"]] = deepcopy(params)
        elif not sql.startswith(("SELECT pg_advisory_xact_lock", "SET TRANSACTION READ ONLY",
                                 "CREATE SCHEMA IF NOT EXISTS app", "CREATE INDEX IF NOT EXISTS idx_ontology_object_relations_")):
            raise AssertionError("Unexpected or non-metadata SQL: " + sql)
        return Result()


class ObjectRelationTests(unittest.IsolatedAsyncioTestCase):
    def test_social_mappings_join_generated_internal_entity_ids_with_explicit_namespaces(self):
        expected = {
            ("content", "author_account_id", "account", "account_id"),
            ("content", "parent_content_id", "content", "content_id"),
            ("content", "root_content_id", "content", "content_id"),
            ("media", "content_id", "content", "content_id"),
            ("content_hashtag", "content_id", "content", "content_id"),
            ("content_hashtag", "hashtag_id", "hashtag", "hashtag_id"),
            ("interaction", "actor_account_id", "account", "account_id"),
            ("interaction", "source_content_id", "content", "content_id"),
            ("interaction", "target_content_id", "content", "content_id"),
        }
        self.assertEqual(len(SOCIAL_SEEDS), 9)
        self.assertEqual({(row["source_table"].removeprefix("ods_social_"), row["source_fields"][0],
                           row["target_table"].removeprefix("ods_social_"), row["target_fields"][0])
                          for row in SOCIAL_SEEDS}, expected)
        tweet = {"id": "300", "author_id": "42", "created_at": "2026-11-16T00:00:00Z",
                 "url": "https://example.invalid/300", "conversation_id": "100",
                 "referenced_tweets": [{"type": "replied_to", "id": "200"}],
                 "media": [{"type": "image", "media_key": "image-1"}], "hashtags": ["Space"]}
        emitted = {"ods_" + row["data_type"]: row for row in normalize_twitter_content(tweet)}
        emitted["ods_social_account"] = normalize_twitter_author({"id": "42"}, None)
        for row in SOCIAL_SEEDS:
            self.assertEqual(validate_definition(row)["cardinality"], "many_to_one")
            self.assertEqual((row["source_schema"], row["target_schema"]), ("ts_ods", "ts_ods"))
            self.assertIn("Twitter platform", row["description"])
            self.assertIn("account_twitter and tweet_twitter are distinct", row["description"])
            self.assertIn("outputs/ods_social_media_design_20261012/ODS社交媒体数据表设计与真实示例.md", row["provenance"])
            self.assertIn("not a physical uniqueness constraint", row["provenance"])
            source_field, target_field = row["source_fields"][0], row["target_fields"][0]
            for side, field in (("source", source_field), ("target", target_field)):
                sql_type = table_columns("ods", row[side + "_table"].removeprefix("ods_"))[field].split()[0]
                self.assertIn(sql_type, {"BIGINT", "BIGSERIAL"})
            source_value = emitted[row["source_table"]][source_field]
            target_value = emitted[row["target_table"]][target_field]
            if source_field == "parent_content_id":
                target_value = normalize_twitter_content({**tweet, "id": "200"})[0][target_field]
            elif source_field == "root_content_id":
                target_value = normalize_twitter_content({**tweet, "id": "100"})[0][target_field]
            elif source_field == "target_content_id":
                target_value = normalize_twitter_content({**tweet, "id": "200"})[0][target_field]
            self.assertIsInstance(source_value, int)
            self.assertGreater(source_value, 0)
            self.assertEqual(source_value, target_value, row["name"])
        self.assertNotEqual(twitter_entity_id("account", "42"), twitter_entity_id("content", "42"))
        self.assertEqual(emitted["ods_social_content"]["content_id"], twitter_entity_id("content", "300"))

    def test_original_social_design_examples_still_produce_the_documented_relationship_ids(self):
        # Original ODS social design sections 4.4, 5.4, 6.4, 7.1/7.2 and 8.4.
        # Minimal fixtures below pin the original identity values, not complete historical records.
        account = normalize_twitter_author({"id": "3247103250"}, None)
        tweet = {"id": "2102839504629350517", "author_id": "3247103250",
                 "created_at": "2026-09-23T00:00:00Z", "url": "https://example.invalid/post",
                 "conversation_id": "2102839504629350517",
                 "media": [{"type": "image", "media_key": "design-image"}], "hashtags": ["WSBW26"]}
        rows = {row["data_type"]: row for row in normalize_twitter_content(tweet)}
        self.assertEqual(account["account_id"], 5961911247415971724)
        self.assertEqual(rows["social_content"]["content_id"], 6214822210978251955)
        self.assertEqual(rows["social_content"]["author_account_id"], account["account_id"])
        self.assertEqual(rows["social_content"]["root_content_id"], rows["social_content"]["content_id"])
        self.assertEqual(rows["social_media"]["content_id"], rows["social_content"]["content_id"])
        self.assertEqual(rows["social_content_hashtag"]["content_id"], rows["social_content"]["content_id"])
        self.assertEqual(rows["social_hashtag"]["hashtag_id"], 2606993089335944644)
        self.assertEqual(rows["social_content_hashtag"]["hashtag_id"], rows["social_hashtag"]["hashtag_id"])
        reply = {**tweet, "id": "2105083326751293662",
                 "referenced_tweets": [{"type": "replied_to", "id": "2105078350507078088"}]}
        rows = {row["data_type"]: row for row in normalize_twitter_content(reply)}
        self.assertEqual(rows["social_content"]["content_id"], 4029308573650902319)
        self.assertEqual(rows["social_content"]["parent_content_id"], 8851059969605173694)
        self.assertEqual(rows["social_interaction"]["actor_account_id"], account["account_id"])
        self.assertEqual(rows["social_interaction"]["source_content_id"], rows["social_content"]["content_id"])
        self.assertEqual(rows["social_interaction"]["target_content_id"], rows["social_content"]["parent_content_id"])

    def test_social_null_references_and_conversation_self_reference_are_not_guessed(self):
        tweet = {"id": "300", "author_id": "42", "created_at": "2026-11-16T00:00:00Z",
                 "url": "https://example.invalid/300", "text": "Reply to 200"}
        emitted = normalize_twitter_content(tweet)
        content = emitted[0]
        self.assertIsNone(content["parent_content_id"])
        self.assertIsNone(content["root_content_id"])
        self.assertFalse(any(row["data_type"] == "social_interaction" for row in emitted))
        root = normalize_twitter_content({**tweet, "conversation_id": "300"})[0]
        self.assertEqual(root["content_id"], root["root_content_id"])
        self.assertIsNone(twitter_entity_id("content", None))

    async def test_social_apply_adds_only_nine_relations_and_preserves_the_existing_29(self):
        pg = MetadataPg(installed=True)
        previous = {row["name"]: deepcopy(row) for row in SEEDS + SPACE_SEEDS}
        pg.records = deepcopy(previous)
        check = await manage_object_relations(pg, ALL_SEEDS, catalog_pg=SimpleNamespace(session=pg.catalog_session))
        self.assertEqual(len(check["changes"]["insert"]), 9)
        self.assertEqual(len(check["changes"]["unchanged"]), 29)
        self.assertEqual(check["changes"]["update"], [])
        self.assertEqual(pg.records, previous)
        self.assertTrue(all(sql.startswith(("SELECT", "SET TRANSACTION READ ONLY")) for sql, _ in pg.calls))
        first = await manage_object_relations(pg, ALL_SEEDS, apply=True, catalog_pg=SimpleNamespace(session=pg.catalog_session))
        self.assertEqual(len(first["changes"]["insert"]), 9)
        self.assertEqual(len(pg.records), 38)
        self.assertEqual({name: pg.records[name] for name in previous}, previous)
        writes = sum(sql.startswith("INSERT") for sql, _ in pg.calls)
        self.assertEqual(writes, 9)
        second = await manage_object_relations(pg, ALL_SEEDS, apply=True, catalog_pg=SimpleNamespace(session=pg.catalog_session))
        self.assertEqual(len(second["changes"]["unchanged"]), 38)
        self.assertEqual(sum(sql.startswith("INSERT") for sql, _ in pg.calls), writes)

    async def test_social_preview_exposes_typed_incoming_outgoing_and_self_references(self):
        relations = [logical(row) for row in SOCIAL_SEEDS]
        instance, _ = service(Result([summary("ts_ods", "ods_social_content", properties_count=0)]),
                              Result([]), Result(relations))
        result = await instance.preview("ts_ods", "ods_social_content")
        linked = {row["table"]: row for row in result["linkedObjectTypes"]}
        self.assertEqual(set(linked), {"ods_social_account", "ods_social_content", "ods_social_media",
                                       "ods_social_content_hashtag", "ods_social_interaction"})
        self.assertEqual(linked["ods_social_account"]["direction"], "outgoing")
        self.assertEqual(linked["ods_social_account"]["dataType"], "social_account")
        self.assertEqual(linked["ods_social_interaction"]["direction"], "incoming")
        self.assertEqual(linked["ods_social_content"]["direction"], "both")
        self.assertEqual(len(linked["ods_social_content"]["relationships"]), 4)
        self.assertTrue(all(not row["constraints"] for row in linked.values()))
        self.assertTrue(all(relation["sourceKind"] == "logical" for row in linked.values()
                            for relation in row["relationships"]))

    async def test_all_documented_space_mappings_are_valid_and_apply_without_duplicate_writes(self):
        space_and_sec = SEEDS + SPACE_SEEDS
        self.assertEqual(len(SPACE_SEEDS), 25)
        self.assertEqual(len(space_and_sec), 29)
        self.assertEqual({row[side + "_table"] for row in SPACE_SEEDS for side in ("source", "target")},
                         {"ods_" + name for name in ("constellation", "satellite", "orbital", "debrisevents", "debris",
                          "launchvehicles", "launchinfo", "launchsites", "frequency", "groundstations", "organization")})
        pg = MetadataPg(installed=True)
        pg.records = {row["name"]: deepcopy(row) for row in SEEDS}
        check = await manage_object_relations(pg, space_and_sec, catalog_pg=SimpleNamespace(session=pg.catalog_session))
        self.assertEqual(len(check["changes"]["insert"]), 25)
        self.assertEqual(len(check["changes"]["unchanged"]), 4)
        self.assertEqual(list(pg.records.values()), SEEDS)
        self.assertTrue(all(sql.startswith(("SELECT", "SET TRANSACTION READ ONLY")) for sql, _ in pg.calls))
        first = await manage_object_relations(pg, space_and_sec, apply=True, catalog_pg=SimpleNamespace(session=pg.catalog_session))
        self.assertEqual(len(first["changes"]["insert"]), 25)
        self.assertEqual(len(first["changes"]["unchanged"]), 4)
        self.assertEqual(len(pg.records), 29)
        writes = sum(sql.startswith("INSERT") for sql, _ in pg.calls)
        second = await manage_object_relations(pg, space_and_sec, apply=True, catalog_pg=SimpleNamespace(session=pg.catalog_session))
        self.assertEqual(len(second["changes"]["unchanged"]), 29)
        self.assertEqual(sum(sql.startswith("INSERT") for sql, _ in pg.calls), writes)

    def test_space_relationship_types_and_source_qualified_array_semantics_match_documented_contract(self):
        array_mappings = []
        for row in SPACE_SEEDS:
            self.assertEqual(validate_definition(row)["cardinality"], "many_to_many")
            self.assertEqual(row["source_schema"], "ts_ods")
            self.assertEqual(row["target_schema"], "ts_ods")
            self.assertIn("docs/Space数据表结构设计.md", row["provenance"])
            self.assertTrue(row["enabled"])
            source = table_columns("ods", row["source_table"].removeprefix("ods_"))
            target = table_columns("ods", row["target_table"].removeprefix("ods_"))
            for left, right in zip(row["source_fields"], row["target_fields"]):
                if source[left] == "JSONB":
                    array_mappings.append(row)
                    self.assertIn("membership", row["description"])
                    self.assertIn(target[right], {"BIGINT", "TEXT"})
                else:
                    self.assertEqual(source[left], target[right])
        self.assertEqual(len(array_mappings), 8)
        by_name = {row["name"]: row for row in SPACE_SEEDS}
        esa = by_name["space_debrisevents_related_debris"]
        self.assertIn("ESA DISCOS", esa["description"])
        self.assertIn("resource_type is object", esa["description"])
        itu = by_name["space_frequency_groundstations"]
        self.assertIn("edition and source context", itu["description"])
        for row in SPACE_SEEDS:
            if row["source_fields"][0].endswith("_codes"):
                self.assertIn("GCAT", row["description"])
        self.assertEqual(by_name["space_launchinfo_launchsites"]["source_fields"], ["site_code", "pad_name"])
        self.assertEqual(by_name["space_launchinfo_launchsites"]["target_fields"], ["site_code", "pad_name"])
        self.assertEqual(by_name["space_launchinfo_launchvehicles"]["source_fields"], ["rocket_name", "vehicle_variant"])
        self.assertEqual(by_name["space_launchinfo_launchvehicles"]["target_fields"], ["name", "variant"])

    async def test_four_verified_sec_seeds_only_reference_actual_ods_fields(self):
        pg = MetadataPg()
        report = await manage_object_relations(pg, SEEDS, catalog_pg=SimpleNamespace(session=pg.catalog_session))
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
        first = await manage_object_relations(pg, SEEDS, apply=True, catalog_pg=SimpleNamespace(session=pg.catalog_session))
        self.assertTrue(first["installed"])
        self.assertEqual(len(pg.records), 4)
        self.assertEqual(pg.commits, 1)
        first_writes = sum(sql.startswith("INSERT") for sql, _ in pg.calls)
        second = await manage_object_relations(pg, SEEDS, apply=True, catalog_pg=SimpleNamespace(session=pg.catalog_session))
        self.assertEqual(len(second["changes"]["unchanged"]), 4)
        self.assertEqual(sum(sql.startswith("INSERT") for sql, _ in pg.calls), first_writes)
        changed = deepcopy(SEEDS)
        changed[0]["enabled"] = False
        changed[0]["description"] = "Explicitly disabled declared relation"
        third = await manage_object_relations(pg, changed, apply=True, catalog_pg=SimpleNamespace(session=pg.catalog_session))
        self.assertEqual(third["changes"]["update"], [SEEDS[0]["name"]])
        self.assertFalse(pg.records[SEEDS[0]["name"]]["enabled"])
        for sql, _ in pg.calls:
            if sql.startswith("INSERT"):
                self.assertTrue(sql.startswith("INSERT INTO app.ontology_object_relations"))
            self.assertNotIn("ALTER TABLE", sql)
            self.assertNotIn("REFERENCES ts_ods", sql)

    async def test_missing_table_or_field_aborts_apply_before_ddl_or_seed_and_rolls_back(self):
        for key in (("ts_ods", "ods_filing"), ("ts_ods", "ods_financial_fact")):
            pg = MetadataPg()
            del pg.catalog[key]
            with self.assertRaisesRegex(ValueError, "missing table or"):
                await manage_object_relations(pg, SEEDS, apply=True, catalog_pg=SimpleNamespace(session=pg.catalog_session))
            self.assertFalse(pg.installed)
            self.assertEqual(pg.records, {})
            self.assertEqual(pg.rollbacks, 1)
            self.assertFalse(any(sql.startswith(("CREATE", "INSERT")) for sql, _ in pg.calls))

    def test_relationship_names_are_display_names_and_keep_identifier_fields_strict(self):
        for name in ("承担企业", "Works for", "作者 · 所属机构"):
            self.assertEqual(validate_definition({**SEEDS[0], "name": name})["name"], name)
        for name in ("", " ", " trailing ", "line\nbreak", "x" * 129, 7):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_definition({**SEEDS[0], "name": name})
        with self.assertRaises(ValueError):
            validate_definition({**SEEDS[0], "relationship_kind": "所属企业"})

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
                await manage_object_relations(pg, [{**SEEDS[0], **mutation}], apply=True, catalog_pg=SimpleNamespace(session=pg.catalog_session))
            self.assertEqual(pg.calls, [])

    async def test_write_failure_rolls_back_table_installation_and_all_prior_seed_records(self):
        pg = MetadataPg()
        pg.fail_on_name = SEEDS[2]["name"]
        with self.assertRaisesRegex(RuntimeError, "rejects a metadata write"):
            await manage_object_relations(pg, SEEDS, apply=True, catalog_pg=SimpleNamespace(session=pg.catalog_session))
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
            await manage_object_relations(pg, [first, reverse], apply=True, catalog_pg=SimpleNamespace(session=pg.catalog_session))
        self.assertEqual(pg.calls, [])
        self.assertEqual(relation_mapping(logical(first)), relation_mapping(logical(reverse)))

    async def test_existing_mapping_cannot_be_duplicated_under_a_new_name(self):
        pg = MetadataPg()
        await manage_object_relations(pg, [SEEDS[0]], apply=True, catalog_pg=SimpleNamespace(session=pg.catalog_session))
        writes = len([sql for sql, _ in pg.calls if sql.startswith("INSERT")])
        with self.assertRaisesRegex(ValueError, "already belongs"):
            await manage_object_relations(pg, [{**SEEDS[0], "name": "different_name"}], apply=True, catalog_pg=SimpleNamespace(session=pg.catalog_session))
        self.assertEqual(len(pg.records), 1)
        self.assertEqual(len([sql for sql, _ in pg.calls if sql.startswith("INSERT")]), writes)

    async def test_preview_includes_outgoing_logical_links_without_claiming_a_physical_constraint(self):
        instance, _ = service(Result([summary("ts_ods", "ods_organization", properties_count=0)]),
                              Result([]), Result([logical(row) for row in SEEDS[:2]]))
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
                                    Result([]), Result([logical(SEEDS[2])]))
        result = await instance.preview("ts_ods", "ods_filing_document")
        self.assertEqual(result["linkedObjectTypes"][0]["id"], "ts_ods.ods_filing")
        self.assertEqual(result["linkedObjectTypes"][0]["dataType"], "filing")
        relation = result["linkedObjectTypes"][0]["relationships"][0]
        self.assertEqual(relation["direction"], "incoming")
        self.assertEqual(relation["columns"], ["accession_number"])
        self.assertEqual(relation["referencedColumns"], ["accession_number"])
        self.assertFalse(any("FROM ts_ods." in sql or "JOIN ts_ods." in sql for sql, _ in session.calls))

    async def test_missing_registry_is_explicit_503_not_a_false_empty_relationship_list(self):
        instance, _ = service(Result([summary(properties_count=0)]), Result([]), relations_installed=False)
        with self.assertRaises(HTTPException) as error:
            await instance.preview("ods", "objects")
        self.assertEqual(error.exception.status_code, 503)
        self.assertIn("not initialized", error.exception.detail)
        instance, session = service(relations_installed=False)
        with self.assertRaises(ObjectRelationsNotInitialized):
            async with instance._metadata_pg.session() as metadata:
                await read_object_relations(metadata, "ts_ods", "objects", catalog_session=session)

    async def test_logical_read_revalidates_tables_columns_scope_and_enabled_without_writes(self):
        instance, session = service(Result([]))
        async with instance._metadata_pg.session() as metadata:
            self.assertEqual(await read_object_relations(metadata, "ts_ods", "ods_filing", catalog_session=session), [])
        sql, params = session.calls[-1]
        self.assertEqual(params, {"schema": "ts_ods", "table": "ods_filing", "object_metadata": "[]", "relation_metadata": "[]"})
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

    async def test_physical_constraints_do_not_create_actionable_relationships(self):
        fk = foreign()
        instance, _ = service(Result([summary(properties_count=0)]),
                              Result([fk]), Result([]))
        self.assertEqual((await instance.preview("ods", "objects"))["linkedObjectTypes"], [])

    def test_storage_ddl_is_only_one_metadata_table_and_has_no_business_foreign_keys(self):
        self.assertEqual(OBJECT_RELATIONS_DDL.count("CREATE TABLE"), 1)
        self.assertIn("CREATE TABLE IF NOT EXISTS app.ontology_object_relations", OBJECT_RELATIONS_DDL)
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
        with patch.object(self.tool, "get_pg_client", return_value=pg), patch.object(self.tool, "get_business_pg_client", return_value=pg), patch("sys.argv", ["object_relations.py", "--check", "--seed-file", str(self.seed_file)]), redirect_stdout(output):
            code = self.tool.main()
        self.assertEqual(code, 1)  # An uninstalled registry is reported, never silently created.
        report = json.loads(output.getvalue())
        self.assertFalse(report["applied"])
        self.assertEqual(report["definitions"], 38)
        self.assertEqual(pg.records, {})
        self.assertTrue(pg.connected and pg.closed)
        self.assertFalse(any(sql.startswith(("CREATE", "INSERT")) for sql, _ in pg.calls))

    def test_cli_apply_installs_and_seeds_only_metadata_and_subsequent_check_is_clean(self):
        pg, output = MetadataPg(), io.StringIO()
        with patch.object(self.tool, "get_pg_client", return_value=pg), patch.object(self.tool, "get_business_pg_client", return_value=pg), patch("sys.argv", ["object_relations.py", "--apply", "--seed-file", str(self.seed_file)]), redirect_stdout(output):
            self.assertEqual(self.tool.main(), 0)
        self.assertEqual(len(pg.records), 38)
        output = io.StringIO()
        with patch.object(self.tool, "get_pg_client", return_value=pg), patch.object(self.tool, "get_business_pg_client", return_value=pg), patch("sys.argv", ["object_relations.py", "--check", "--seed-file", str(self.seed_file)]), redirect_stdout(output):
            self.assertEqual(self.tool.main(), 0)
        self.assertEqual(len(json.loads(output.getvalue())["changes"]["unchanged"]), 38)
