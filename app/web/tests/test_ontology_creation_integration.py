"""Real creation checks limited to the disposable ontology_creation_regression database."""

import os
import unittest
from uuid import uuid4
from copy import deepcopy
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.etl.schema import ontology
from app.etl.schema.object_relations import OBJECT_RELATIONS_DDL_STATEMENTS
from app.etl.schema.object_links import OBJECT_LINKS_DDL_STATEMENTS
from app.storage.postgres_client import PostgresClient
from app.web.api.models.etl import PreviewCompositeIndexCoverageRequest, PreviewIndexCoverageRequest, PreviewSortIndexCoverageRequest
from app.web.api.models.indexed_id_sample import IndexedCompoundSampleRequest, IndexedSampleRequest
from app.web.core.config import settings
from app.web.core.config.indexed_sample import IndexedCompoundSampleScope, IndexedSampleScope
from app.web.services import indexed_compound_id_sample as compound, indexed_id_sample as indexed
from app.web.services.etl_layers import EtlLayers
from app.web.services.etl_workspace import EtlWorkspace
from app.web.services.preview_index_coverage import preview_index_coverage
from app.web.services.lineage_column_references import read_lineage_column_references


URL = os.environ.get("ONTOLOGY_TEST_DATABASE_URL")
DATABASE = "ontology_creation_regression"
SCHEMA = "ts_creation"
TARGET_SCHEMA = "ts_creation_targets"
PAYLOAD = {"name": "firms", "dataType": "company", "description": "Companies registered through Flow",
    "fields": [
        {"name": "record_id", "type": "TEXT", "primary": True, "nullable": False, "comment": "Business record identifier"},
        {"name": "company_name", "type": "VARCHAR", "length": "160", "nullable": False, "comment": "Registered company name"}],
    "indexes": [{"name": "company_name_lookup", "type": "BTREE", "fields": "company_name", "unique": False}],
    "checks": [{"name": "company_name_nonempty", "expression": "length(company_name) > 0"}],
    "options": {"comment": "Companies registered through Flow"}}


@unittest.skipUnless(URL, "Requires an isolated ontology_creation_regression PostgreSQL instance")
class OntologyCreationIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Reject other database names before even opening a connection.
        if make_url(URL).database != DATABASE:
            raise RuntimeError("Creation tests refuse any database except ontology_creation_regression")
        self.pg = PostgresClient(URL)
        await self.pg.connect(max_retries=1)
        if (await self.pg.fetch_one("SELECT current_database() AS name"))["name"] != DATABASE:
            await self.pg.close()
            raise RuntimeError("Connected database is not ontology_creation_regression")
        async with self.pg.session() as session:
            for schema in (SCHEMA, TARGET_SCHEMA):
                await session.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
                await session.execute(text(f"CREATE SCHEMA {schema}"))
            for statement in (*ontology.ONTOLOGY_DDL_STATEMENTS, *OBJECT_RELATIONS_DDL_STATEMENTS, *OBJECT_LINKS_DDL_STATEMENTS):
                await session.execute(text(statement))
            await session.execute(text("""DELETE FROM app.ontology_object_links WHERE relation_id IN (
                SELECT id FROM app.ontology_object_relations
                WHERE source_schema IN (:source, :target) OR target_schema IN (:source, :target))"""),
                {"source": SCHEMA, "target": TARGET_SCHEMA})
            await session.execute(text("""DELETE FROM app.ontology_object_relations
                WHERE source_schema IN (:source, :target) OR target_schema IN (:source, :target)"""),
                {"source": SCHEMA, "target": TARGET_SCHEMA})
            await session.execute(text("DELETE FROM app.ontology_objects WHERE schema_name IN (:source, :target)"),
                {"source": SCHEMA, "target": TARGET_SCHEMA})
        self.layers = object.__new__(EtlLayers)
        self.layers._pg = self.pg
        self.workspace = object.__new__(EtlWorkspace)
        self.workspace._pg, self.workspace._layers = self.pg, self.layers

    async def asyncTearDown(self):
        await self.pg.close()

    async def metadata(self, table="firms"):
        return await self.pg.fetch_one("SELECT * FROM app.ontology_objects WHERE schema_name=:schema AND table_name=:table",
                                       {"schema": SCHEMA, "table": table})

    async def assert_absent(self, table):
        self.assertIsNone((await self.pg.fetch_one("SELECT to_regclass(:qualified) AS name", {"qualified": f"{SCHEMA}.{table}"}))["name"])
        self.assertIsNone(await self.metadata(table))
        self.assertEqual(await self.pg.fetch_all("""SELECT p.name FROM app.ontology_object_properties p
            JOIN app.ontology_objects o ON o.id=p.object_id WHERE o.schema_name=:schema AND o.table_name=:table""",
            {"schema": SCHEMA, "table": table}), [])

    async def relation_payload(self):
        await self.workspace.create_table(TARGET_SCHEMA, {"name": "companies", "dataType": "company",
            "fields": deepcopy(PAYLOAD["fields"])})
        return {**deepcopy(PAYLOAD), "relations": [{"name": "firms_company", "targetSchema": TARGET_SCHEMA,
            "targetTable": "companies", "sourceFields": ["record_id"], "targetFields": ["record_id"],
            "cardinality": "many_to_one", "description": "Parent company"}]}

    async def test_exact_authorized_index_sample_returns_only_canonical_ids_and_reproves_after_reindex(self):
        marker = uuid4()
        marker_table = "public.ontology_explorer_indexed_sample_identity"
        async with self.pg.session() as session:
            await session.execute(text(f"CREATE TABLE {marker_table} (marker uuid PRIMARY KEY)"))
            await session.execute(text(f"INSERT INTO {marker_table} VALUES (:marker)"), {"marker": marker})
            await session.execute(text(f"CREATE TABLE {SCHEMA}.narrow (record_id bigint PRIMARY KEY, secret text NOT NULL)"))
            await session.execute(text(f"INSERT INTO {SCHEMA}.narrow SELECT key, 'never returned' FROM generate_series(1,10000) key"))
            await session.execute(text(f"INSERT INTO {SCHEMA}.narrow VALUES (-9223372036854775808,'extra'), (9223372036854775807,'extra')"))
            await session.execute(text(f"ANALYZE {SCHEMA}.narrow"))
            scope = (await session.execute(text(f"""
                SELECT current_database() AS database, '{SCHEMA}' AS schema, 'narrow' AS "table", 'record_id' AS "keyColumn",
                    c.oid::bigint AS "heapOid", pg_relation_filenode(c.oid)::bigint AS "heapFilenode",
                    i.indexrelid::bigint AS "indexOid", pg_relation_filenode(i.indexrelid)::bigint AS "indexFilenode"
                FROM pg_class c JOIN pg_index i ON i.indrelid=c.oid AND i.indisprimary
                WHERE c.oid='{SCHEMA}.narrow'::regclass
            """))).mappings().one()
        self.addAsyncCleanup(self.pg.execute, f"DROP TABLE {marker_table}")
        approval = IndexedSampleScope.model_validate(scope)

        async def capability():
            async with self.pg.session() as session:
                return await indexed.indexed_sample_capability(session, SCHEMA, "narrow")

        async def sample(**bounds):
            async with self.pg.session() as session:
                return await indexed.sample_indexed_ids(session, SCHEMA, "narrow", IndexedSampleRequest(**bounds))

        with (patch.object(settings, "ENVIRONMENT", "local"),
              patch.object(settings, "ONTOLOGY_EXPLORER_INDEXED_SAMPLE_ALLOWLIST", [approval]),
              patch.object(settings, "ONTOLOGY_EXPLORER_INDEXED_SAMPLE_DATABASE_MARKER", marker)):
            self.assertFalse((await capability()).available)
            with patch.object(settings, "ONTOLOGY_EXPLORER_INDEXED_SAMPLE_ENABLED", True):
                with patch.object(settings, "ONTOLOGY_EXPLORER_INDEXED_SAMPLE_DATABASE_MARKER", uuid4()):
                    self.assertFalse((await capability()).available)
                    self.assertEqual((await sample(idEquals="1")).reason, "scope_unavailable")
                self.assertEqual((await capability()).model_dump(), {
                    "available": True, "keyColumn": "record_id", "limit": 20,
                })
                edge = await sample(idEquals="-9223372036854775808")
                self.assertEqual(edge.model_dump(), {"state": "sampled", "reason": "index_path",
                                                    "ids": ["-9223372036854775808"], "hasMore": False})
                result = await sample(idFrom="1", idTo="50")
                self.assertEqual(result.ids, [str(i) for i in range(1, 21)])
                self.assertTrue(result.hasMore)
                async with self.pg.session() as writer:
                    await writer.execute(text(f"LOCK TABLE ONLY {SCHEMA}.narrow IN ROW EXCLUSIVE MODE"))
                    with patch.object(indexed, "_approved_plan", side_effect=AssertionError("busy table reached EXPLAIN")):
                        busy = await sample(idEquals="5")
                    self.assertEqual((busy.reason, busy.ids), ("query_unavailable", []))
                original_limits = indexed._limits

                async def force_residual(session):
                    await original_limits(session)
                    for knob in ("enable_indexscan", "enable_indexonlyscan", "enable_bitmapscan"):
                        await session.execute(text(f"SET LOCAL {knob}=off"))

                with patch.object(indexed, "_limits", force_residual):
                    blocked = await sample(idEquals="5")
                    self.assertEqual((blocked.reason, blocked.ids), ("unsupported_plan", []))
                await self.workspace.set_object_visibility(SCHEMA, "narrow", "hidden", "fixture")
                self.assertFalse((await capability()).available)
                self.assertEqual((await sample(idEquals="5")).reason, "scope_unavailable")
                await self.workspace.set_object_visibility(SCHEMA, "narrow", "normal", "fixture")
                await self.pg.execute(f"REINDEX INDEX {SCHEMA}.narrow_pkey")
                self.assertFalse((await capability()).available)
                self.assertEqual((await sample(idEquals="5")).reason, "scope_unavailable")

    async def test_verified_compound_id_sample_requires_exact_native_prefix_and_both_bounds(self):
        marker = uuid4()
        marker_table = "public.ontology_explorer_indexed_sample_identity"
        table = f"{SCHEMA}.compound_ids"
        async with self.pg.session() as session:
            for sql in (
                f"CREATE TABLE {marker_table} (marker uuid PRIMARY KEY)",
                f"CREATE TABLE {table} (tenant_id bigint, group_id bigint, id bigint, secret text, "
                "PRIMARY KEY (tenant_id,group_id,id))",
                f"INSERT INTO {table} SELECT tenant, grp, id, 'never returned' "
                "FROM generate_series(1,100) tenant, generate_series(1,20) grp, generate_series(1,80) id",
                f"INSERT INTO {table} VALUES(-9223372036854775808,9223372036854775807,-9223372036854775808,'secret')",
                f"ANALYZE {table}",
            ):
                await session.execute(text(sql))
            await session.execute(text(f"INSERT INTO {marker_table} VALUES (:marker)"), {"marker": marker})
            profile = (await session.execute(text(f"""
                SELECT current_database() AS database, '{SCHEMA}' AS schema, 'compound_ids' AS "table",
                    c.oid::bigint AS "heapOid", pg_relation_filenode(c.oid)::bigint AS "heapFilenode",
                    i.indexrelid::bigint AS "indexOid", pg_relation_filenode(i.indexrelid)::bigint AS "indexFilenode"
                FROM pg_class c JOIN pg_index i ON i.indrelid=c.oid AND i.indisprimary
                WHERE c.oid='{table}'::regclass
            """))).mappings().one()
        self.addAsyncCleanup(self.pg.execute, f"DROP TABLE {marker_table}")
        profile = IndexedCompoundSampleScope.model_validate(profile)
        request = IndexedCompoundSampleRequest(tenantId="2", groupId="5", idFrom="4", idTo="41")

        async def cap():
            async with self.pg.session() as session:
                return await compound.indexed_compound_sample_capability(session, SCHEMA, "compound_ids")

        async def sample(payload=request):
            async with self.pg.session() as session:
                return await compound.sample_indexed_compound_ids(session, SCHEMA, "compound_ids", payload)

        with (patch.object(settings, "ENVIRONMENT", "local"),
              patch.object(settings, "ONTOLOGY_EXPLORER_INDEXED_COMPOUND_SAMPLE_PROFILE", profile),
              patch.object(settings, "ONTOLOGY_EXPLORER_INDEXED_SAMPLE_DATABASE_MARKER", marker)):
            self.assertFalse((await cap()).available)
            with patch.object(settings, "ONTOLOGY_EXPLORER_INDEXED_COMPOUND_SAMPLE_ENABLED", True):
                self.assertEqual((await cap()).model_dump(), {"available": True, "limit": 20})
                positive = await sample()
                self.assertEqual(positive.ids, [str(n) for n in range(4, 24)], positive.model_dump())
                self.assertTrue(positive.hasMore)
                exact = await sample(IndexedCompoundSampleRequest(
                    tenantId="-9223372036854775808", groupId="9223372036854775807",
                    idFrom="-9223372036854775808", idTo="9223372036854775807",
                ))
                self.assertEqual(exact.ids, ["-9223372036854775808"], exact.model_dump())
                self.assertFalse(exact.hasMore)
                async with self.pg.session() as writer:
                    await writer.execute(text(f"LOCK TABLE ONLY {table} IN ROW EXCLUSIVE MODE"))
                    with patch.object(compound, "_approved_plan", side_effect=AssertionError("busy table reached EXPLAIN")):
                        self.assertEqual((await sample()).reason, "query_unavailable")
                original_limits = compound._limits

                async def without_index(session):
                    await original_limits(session)
                    for knob in ("enable_indexscan", "enable_indexonlyscan", "enable_bitmapscan"):
                        await session.execute(text(f"SET LOCAL {knob}=off"))

                with patch.object(compound, "_limits", without_index):
                    self.assertEqual((await sample()).reason, "unsupported_plan")
                await self.pg.execute(f"REINDEX INDEX {SCHEMA}.compound_ids_pkey")
                self.assertFalse((await cap()).available)
                self.assertEqual((await sample()).reason, "scope_unavailable")

    async def test_index_coverage_uses_only_visible_native_leading_keys_and_column_privileges(self):
        role = "helio_coverage_" + uuid4().hex[:12]
        async with self.pg.session() as session:
            for sql in (
                f"CREATE DOMAIN {SCHEMA}.test_id AS bigint",
                f"""CREATE TABLE {SCHEMA}.shape (
                    id bigint PRIMARY KEY, second_key date, happened_at timestamptz, local_time timestamp,
                    partial_key int, included int, expression_key int, plain int, code text, code_id {SCHEMA}.test_id
                )""",
                f"CREATE INDEX ON {SCHEMA}.shape (id, second_key) INCLUDE (included)",
                f"CREATE INDEX ON {SCHEMA}.shape (happened_at DESC)",
                f"CREATE INDEX ON {SCHEMA}.shape (partial_key) WHERE partial_key > 0",
                f"CREATE INDEX ON {SCHEMA}.shape ((expression_key + 1))",
                f"CREATE INDEX ON {SCHEMA}.shape (plain, (expression_key + 1))",
                f"CREATE INDEX ON {SCHEMA}.shape (code_id)",
                f"CREATE ROLE {role} NOLOGIN",
                f"GRANT SELECT ON app.ontology_objects TO {role}",
                f"GRANT USAGE ON SCHEMA {SCHEMA} TO {role}",
                f"GRANT SELECT (id) ON {SCHEMA}.shape TO {role}",
            ):
                await session.execute(text(sql))

        async def cleanup():
            await self.pg.execute(f"DROP OWNED BY {role}")
            await self.pg.execute(f"DROP ROLE {role}")

        self.addAsyncCleanup(cleanup)

        async def probe(field, operator="eq", as_reader=False, table="shape"):
            async with self.pg.session() as session:
                if as_reader:
                    await session.execute(text(f"SET LOCAL ROLE {role}"))
                return (await preview_index_coverage(session, SCHEMA, table, PreviewIndexCoverageRequest(
                    field=field, operator=operator,
                ))).state

        for operator in ("eq", "lt", "lte", "gt", "gte", "between"):
            self.assertEqual(await probe("id", operator), "candidate")
        self.assertEqual(await probe("happened_at", "between"), "candidate")
        for field in ("second_key", "partial_key", "included", "expression_key", "plain", "local_time", "code"):
            self.assertEqual(await probe(field), "no_candidate", field)
        for field in ("code_id",):
            self.assertEqual(await probe(field), "unsupported", field)
        self.assertEqual(await probe("id", as_reader=True), "candidate")
        self.assertEqual(await probe("happened_at", as_reader=True), "unavailable")
        self.assertEqual(await probe("absent", as_reader=True), "unavailable")
        await self.workspace.set_object_visibility(SCHEMA, "shape", "hidden", "fixture")
        for name in ("shape", "absent"):
            with self.assertRaises(HTTPException) as error:
                await probe("id", table=name)
            self.assertEqual((error.exception.status_code, error.exception.detail),
                             (404, "Table does not exist in this schema"))

    async def test_composite_index_coverage_requires_one_native_ordered_prefix_after_acl(self):
        role = "helio_compound_coverage_" + uuid4().hex[:12]
        async with self.pg.session() as session:
            for sql in (
                f"""CREATE TABLE {SCHEMA}.shapes (
                    a bigint, b date, c timestamptz, payload int, separate int,
                    partial_one int, partial_two int, expr_one int, expr_two int, invalid_order int,
                    kind text
                )""",
                f"CREATE INDEX ON {SCHEMA}.shapes (a, b DESC, c, invalid_order) INCLUDE (payload)",
                f"CREATE INDEX ON {SCHEMA}.shapes (separate)",
                f"CREATE INDEX ON {SCHEMA}.shapes (partial_one, partial_two) WHERE partial_one > 0",
                f"CREATE INDEX ON {SCHEMA}.shapes (expr_one, (expr_two + 1))",
                f"CREATE INDEX ON {SCHEMA}.shapes USING hash (expr_two)",
                f"CREATE INDEX ON {SCHEMA}.shapes (expr_one, expr_two, (separate + 1))",
                f"CREATE ROLE {role} NOLOGIN",
                f"GRANT SELECT ON app.ontology_objects TO {role}",
                f"GRANT USAGE ON SCHEMA {SCHEMA} TO {role}",
                f"GRANT SELECT (a, b, kind) ON {SCHEMA}.shapes TO {role}",
            ):
                await session.execute(text(sql))

        async def cleanup():
            await self.pg.execute(f"DROP OWNED BY {role}")
            await self.pg.execute(f"DROP ROLE {role}")

        self.addAsyncCleanup(cleanup)

        async def probe(*terms, reader=False):
            payload = PreviewCompositeIndexCoverageRequest(terms=[
                PreviewIndexCoverageRequest(field=field, operator=operator) for field, operator in terms
            ])
            async with self.pg.session() as session:
                if reader:
                    await session.execute(text(f"SET LOCAL ROLE {role}"))
                return (await preview_index_coverage(session, SCHEMA, "shapes", payload)).state

        self.assertEqual(await probe(("b", "between"), ("a", "eq"), reader=True), "candidate")
        self.assertEqual(await probe(("c", "lt"), ("a", "eq"), ("b", "eq")), "candidate")
        self.assertEqual(await probe(("c", "eq"), ("b", "eq"), ("a", "eq")), "candidate")
        for comparison in (
            (("a", "gte"), ("b", "eq")), (("a", "eq"), ("c", "eq")),
            (("a", "eq"), ("b", "eq"), ("payload", "eq")),
            (("a", "eq"), ("b", "eq"), ("invalid_order", "eq")),
            (("a", "eq"), ("separate", "eq")), (("partial_one", "eq"), ("partial_two", "eq")),
            (("expr_one", "eq"), ("expr_two", "eq")),
        ):
            self.assertEqual(await probe(*comparison), "no_candidate", comparison)
        self.assertEqual(await probe(("a", "eq"), ("kind", "eq"), reader=True), "no_candidate")
        self.assertEqual(await probe(("c", "eq"), ("kind", "eq"), reader=True), "unavailable")
        self.assertEqual(await probe(("a", "eq"), ("does_not_exist", "eq"), reader=True), "unavailable")
        await self.pg.execute(f"REVOKE USAGE ON SCHEMA {SCHEMA} FROM {role}")
        with self.assertRaises(HTTPException) as error:
            await probe(("a", "eq"), ("b", "eq"), reader=True)
        self.assertEqual(error.exception.status_code, 404)

    async def test_filter_index_coverage_supports_common_native_types_and_collation_after_acl(self):
        role = "helio_scalar_coverage_" + uuid4().hex[:12]
        async with self.pg.session() as session:
            for sql in (
                f"CREATE DOMAIN {SCHEMA}.scalar_id AS bigint",
                f"""CREATE TABLE {SCHEMA}.scalar_shapes (
                    code text, label varchar(80), padded char(8), amount numeric(20,3),
                    ratio_real real, ratio_double double precision, active boolean, token uuid,
                    local_time timestamp, missing_text text, different_collation text,
                    pattern_label varchar(80), custom_type {SCHEMA}.scalar_id, document jsonb
                )""",
                *(f"CREATE INDEX ON {SCHEMA}.scalar_shapes ({field})" for field in (
                    "label", "padded", "amount", "ratio_real", "ratio_double", "active", "token", "local_time",
                )),
                f"CREATE INDEX ON {SCHEMA}.scalar_shapes (code, amount, local_time)",
                f'CREATE INDEX ON {SCHEMA}.scalar_shapes (different_collation COLLATE "C")',
                f"CREATE INDEX ON {SCHEMA}.scalar_shapes (pattern_label varchar_pattern_ops)",
                f"CREATE INDEX ON {SCHEMA}.scalar_shapes (custom_type)",
                f"CREATE ROLE {role} NOLOGIN",
                f"GRANT SELECT ON app.ontology_objects TO {role}",
                f"GRANT USAGE ON SCHEMA {SCHEMA} TO {role}",
                f"GRANT SELECT (code, label) ON {SCHEMA}.scalar_shapes TO {role}",
            ):
                await session.execute(text(sql))

        async def cleanup():
            await self.pg.execute(f"DROP OWNED BY {role}")
            await self.pg.execute(f"DROP ROLE {role}")

        self.addAsyncCleanup(cleanup)

        async def probe(*terms, reader=False):
            shapes = [PreviewIndexCoverageRequest(field=field, operator=operator) for field, operator in terms]
            payload = shapes[0] if len(shapes) == 1 else PreviewCompositeIndexCoverageRequest(terms=shapes)
            async with self.pg.session() as session:
                if reader:
                    await session.execute(text(f"SET LOCAL ROLE {role}"))
                return (await preview_index_coverage(session, SCHEMA, "scalar_shapes", payload)).state

        for field in ("code", "label", "padded", "amount", "ratio_real", "ratio_double", "active", "token", "local_time"):
            for operator in ("eq", "lt", "lte", "gt", "gte", "between"):
                self.assertEqual(await probe((field, operator)), "candidate", (field, operator))
        for field in ("missing_text", "different_collation", "pattern_label"):
            self.assertEqual(await probe((field, "eq")), "no_candidate", field)
        for field in ("custom_type", "document"):
            self.assertEqual(await probe((field, "eq")), "unsupported", field)
        self.assertEqual(await probe(("code", "eq"), ("amount", "gte")), "candidate")
        self.assertEqual(await probe(("local_time", "between"), ("code", "eq"), ("amount", "eq")), "candidate")
        self.assertEqual(await probe(("code", "eq"), ("local_time", "eq")), "no_candidate")
        self.assertEqual(await probe(("label", "eq"), reader=True), "candidate")
        self.assertEqual(await probe(("amount", "eq"), reader=True), "unavailable")
        self.assertEqual(await probe(("document", "eq"), reader=True), "unavailable")
        self.assertEqual(await probe(("missing", "eq"), reader=True), "unavailable")

    async def test_sort_index_coverage_matches_complete_direction_nulls_collation_and_acl(self):
        role = "helio_sort_coverage_" + uuid4().hex[:12]
        async with self.pg.session() as session:
            for sql in (
                f"CREATE DOMAIN {SCHEMA}.sort_id AS bigint",
                f"""CREATE TABLE {SCHEMA}.sort_shapes (
                    a bigint, b date, c timestamptz, included int, plain int, partial_key int,
                    expression_key int, null_order int, code text, label varchar(80),
                    different_collation text, custom_type {SCHEMA}.sort_id, hash_key int
                )""",
                f"CREATE INDEX ON {SCHEMA}.sort_shapes (a, b DESC, c) INCLUDE (included)",
                f"CREATE INDEX ON {SCHEMA}.sort_shapes (partial_key) WHERE partial_key > 0",
                f"CREATE INDEX ON {SCHEMA}.sort_shapes (plain, (expression_key + 1))",
                f"CREATE INDEX ON {SCHEMA}.sort_shapes (null_order DESC NULLS LAST)",
                f"CREATE INDEX ON {SCHEMA}.sort_shapes (code DESC)",
                f"CREATE INDEX ON {SCHEMA}.sort_shapes (label)",
                f'CREATE INDEX ON {SCHEMA}.sort_shapes (different_collation COLLATE "C")',
                f"CREATE INDEX ON {SCHEMA}.sort_shapes (custom_type)",
                f"CREATE INDEX ON {SCHEMA}.sort_shapes USING hash (hash_key)",
                f"CREATE ROLE {role} NOLOGIN",
                f"GRANT SELECT ON app.ontology_objects TO {role}",
                f"GRANT USAGE ON SCHEMA {SCHEMA} TO {role}",
                f"GRANT SELECT (a, b, code) ON {SCHEMA}.sort_shapes TO {role}",
            ):
                await session.execute(text(sql))

        async def cleanup():
            await self.pg.execute(f"DROP OWNED BY {role}")
            await self.pg.execute(f"DROP ROLE {role}")

        self.addAsyncCleanup(cleanup)

        async def probe(*sorts, reader=False, table="sort_shapes"):
            payload = PreviewSortIndexCoverageRequest(sorts=[{"field": field, "direction": direction} for field, direction in sorts])
            async with self.pg.session() as session:
                if reader:
                    await session.execute(text(f"SET LOCAL ROLE {role}"))
                return (await preview_index_coverage(session, SCHEMA, table, payload)).state

        for sorts in (
            (("a", "asc"),), (("a", "desc"),), (("code", "asc"),), (("code", "desc"),),
            (("label", "asc"),), (("a", "asc"), ("b", "desc")),
            (("a", "desc"), ("b", "asc")), (("a", "asc"), ("b", "desc"), ("c", "asc")),
            (("a", "desc"), ("b", "asc"), ("c", "desc")),
        ):
            self.assertEqual(await probe(*sorts), "candidate", sorts)
        for sorts in (
            (("b", "desc"),), (("b", "desc"), ("a", "asc")),
            (("a", "asc"), ("b", "asc")), (("a", "asc"), ("b", "desc"), ("c", "desc")),
            (("a", "asc"), ("included", "asc")), (("plain", "asc"),),
            (("partial_key", "asc"),), (("expression_key", "asc"),),
            (("null_order", "asc"),), (("null_order", "desc"),),
            (("different_collation", "asc"),), (("hash_key", "asc"),),
        ):
            self.assertEqual(await probe(*sorts), "no_candidate", sorts)
        self.assertEqual(await probe(("custom_type", "asc")), "unsupported")
        self.assertEqual(await probe(("a", "asc"), ("b", "desc"), reader=True), "candidate")
        self.assertEqual(await probe(("a", "asc"), ("c", "asc"), reader=True), "unavailable")
        self.assertEqual(await probe(("custom_type", "asc"), reader=True), "unavailable")
        self.assertEqual(await probe(("absent", "asc"), reader=True), "unavailable")
        await self.workspace.set_object_visibility(SCHEMA, "sort_shapes", "hidden", "fixture")
        for table in ("sort_shapes", "absent"):
            with self.assertRaises(HTTPException) as error:
                await probe(("a", "asc"), table=table)
            self.assertEqual(error.exception.status_code, 404)

    async def test_lineage_neighbors_are_direct_typed_visible_and_bounded(self):
        await self.workspace.create_table(SCHEMA, await self.relation_payload())
        history = f"{SCHEMA}_hist"
        async with self.pg.session() as session:
            await session.execute(text(f"DROP SCHEMA IF EXISTS {history} CASCADE"))
            await session.execute(text(f"CREATE SCHEMA {history}"))
            await session.execute(text(f"""CREATE TABLE {history}.not_current
                (record_id text REFERENCES {SCHEMA}.firms(record_id))"""))
            await session.execute(text(f"""ALTER TABLE {SCHEMA}.firms ADD CONSTRAINT firms_company_fk
                FOREIGN KEY (record_id) REFERENCES {TARGET_SCHEMA}.companies(record_id)"""))
            for index in range(14):
                await session.execute(text(f"""CREATE TABLE {SCHEMA}.peer_{index:02}
                    (record_id text REFERENCES {SCHEMA}.firms(record_id))"""))
            await session.execute(text(f"CREATE VIEW {SCHEMA}.not_table AS SELECT * FROM {SCHEMA}.firms"))
            await session.execute(text(f"""
                INSERT INTO app.ontology_object_relations
                    (name, source_schema, source_table, source_fields, target_schema, target_table,
                     target_fields, relationship_kind, cardinality, provenance, enabled)
                VALUES ('missing_column', :schema, 'firms', ARRAY['removed'], :schema,
                        'peer_00', ARRAY['record_id'], 'association', 'many_to_one', 'test', true),
                       ('disabled_link', :schema, 'firms', ARRAY['record_id'], :schema,
                        'peer_01', ARRAY['record_id'], 'association', 'many_to_one', 'test', false),
                       ('view_link', :schema, 'firms', ARRAY['record_id'], :schema,
                        'not_table', ARRAY['record_id'], 'association', 'many_to_one', 'test', true)
            """), {"schema": SCHEMA})

        outgoing = await self.layers.lineage_neighbors(SCHEMA, "firms", "outgoing")
        self.assertEqual(outgoing.model_dump(by_alias=True), {"direction": "outgoing", "truncated": False, "neighbors": [
            {"schema": TARGET_SCHEMA, "table": "companies", "kinds": ["foreign_key", "object_relation"]}]})

        incoming = await self.layers.lineage_neighbors(SCHEMA, "firms", "incoming")
        self.assertTrue(incoming.truncated)
        self.assertEqual([node.table for node in incoming.neighbors], [f"peer_{i:02}" for i in range(12)])
        self.assertTrue(all(node.kinds == ["foreign_key"] for node in incoming.neighbors))

        for table in ("peer_00", "peer_01"):
            await self.workspace.set_object_visibility(SCHEMA, table, "hidden", None)
        incoming = await self.layers.lineage_neighbors(SCHEMA, "firms", "incoming")
        self.assertFalse(incoming.truncated)
        self.assertEqual([node.table for node in incoming.neighbors], [f"peer_{i:02}" for i in range(2, 14)])

        for schema, table in ((SCHEMA, "peer_00"), (SCHEMA, "not_table"),
                              (history, "not_current"), ("creation", "firms")):
            with self.assertRaises(HTTPException) as error:
                await self.layers.lineage_neighbors(schema, table, "outgoing")
            self.assertEqual(error.exception.status_code, 404)

    async def test_direct_column_references_zip_validated_composite_keys_and_declared_fields_by_ordinal(self):
        role = "helio_column_" + uuid4().hex[:12]
        async with self.pg.session() as session:
            for sql in (
                f"CREATE TABLE {SCHEMA}.parent (x bigint, y bigint, z bigint, UNIQUE(y, x))",
                f"CREATE TABLE {SCHEMA}.child (b bigint, a bigint, extra bigint, FOREIGN KEY(a,b) REFERENCES {SCHEMA}.parent(y,x))",
                f"""ALTER TABLE {SCHEMA}.child ADD CONSTRAINT unvalidated FOREIGN KEY (extra,a)
                     REFERENCES {SCHEMA}.parent(y,x) NOT VALID""",
                f"CREATE TABLE {SCHEMA}.tree (id bigint PRIMARY KEY, parent_id bigint REFERENCES {SCHEMA}.tree(id))",
                f"CREATE ROLE {role} NOLOGIN",
                f"GRANT USAGE ON SCHEMA {SCHEMA} TO {role}",
                f"GRANT SELECT ON app.ontology_objects, app.ontology_object_relations TO {role}",
                f"GRANT SELECT (a) ON {SCHEMA}.child TO {role}",
            ):
                await session.execute(text(sql))
            await session.execute(text("""
                INSERT INTO app.ontology_object_relations (
                    name, source_schema, source_table, source_fields, target_schema, target_table, target_fields,
                    relationship_kind, cardinality, provenance
                ) VALUES
                    ('column_ordinal_valid', :schema, 'child', ARRAY['a','b'], :schema, 'parent',
                     ARRAY['y','x'], 'association', 'many_to_one','test'),
                    ('column_ordinal_broken', :schema, 'child', ARRAY['a','removed'], :schema, 'parent',
                     ARRAY['z','x'], 'association', 'many_to_one','test')
            """), {"schema": SCHEMA})

        async def cleanup():
            await self.pg.execute(f"DROP OWNED BY {role}")
            await self.pg.execute(f"DROP ROLE {role}")

        self.addAsyncCleanup(cleanup)

        async def refs(table, column, direction, *, as_reader=False):
            async with self.pg.session() as session:
                if as_reader:
                    await session.execute(text(f"SET LOCAL ROLE {role}"))
                return (await read_lineage_column_references(session, SCHEMA, table, column, direction)).model_dump(by_alias=True)

        dual = ["foreign_key", "object_relation"]
        self.assertEqual((await refs("child", "a", "outgoing"))["references"],
                         [{"schema": SCHEMA, "table": "parent", "column": "y", "kinds": dual}])
        self.assertEqual((await refs("child", "b", "outgoing"))["references"][0]["column"], "x")
        self.assertEqual((await refs("parent", "x", "incoming"))["references"],
                         [{"schema": SCHEMA, "table": "child", "column": "b", "kinds": dual}])
        self.assertEqual((await refs("child", "extra", "outgoing"))["references"], [])
        self.assertEqual((await refs("tree", "parent_id", "outgoing"))["references"],
                         [{"schema": SCHEMA, "table": "tree", "column": "id", "kinds": ["foreign_key"]}])
        self.assertEqual((await refs("child", "a", "outgoing", as_reader=True))["references"], [])
        for column in ("y", "missing"):
            with self.assertRaises(HTTPException) as error:
                await refs("parent", column, "outgoing", as_reader=True)
            self.assertEqual((error.exception.status_code, error.exception.detail),
                             (404, "Table or column reference is unavailable"))
        await self.pg.execute(f"GRANT SELECT (y) ON {SCHEMA}.parent TO {role}")
        self.assertEqual((await refs("child", "a", "outgoing", as_reader=True))["references"][0]["kinds"], dual)
        await self.pg.execute(f"REVOKE SELECT ON app.ontology_object_relations FROM {role}")
        self.assertEqual((await refs("child", "a", "outgoing", as_reader=True))["references"][0]["kinds"], ["foreign_key"])
        await self.pg.execute(f"REVOKE USAGE ON SCHEMA {SCHEMA} FROM {role}")
        with self.assertRaises(HTTPException) as error:
            await refs("child", "a", "outgoing", as_reader=True)
        self.assertEqual(error.exception.status_code, 404)

        await self.workspace.set_object_visibility(SCHEMA, "parent", "hidden", "fixture")
        self.assertEqual((await refs("child", "a", "outgoing"))["references"], [])
        await self.workspace.set_object_visibility(SCHEMA, "parent", "normal", "fixture")
        await self.pg.execute("DROP TABLE app.ontology_object_links")
        await self.pg.execute("DROP TABLE app.ontology_object_relations")
        self.assertEqual((await refs("child", "a", "outgoing"))["references"][0]["kinds"], ["foreign_key"])

    async def test_column_reference_cap_applies_after_visibility(self):
        async with self.pg.session() as session:
            await session.execute(text(f"CREATE TABLE {SCHEMA}.parent (id bigint PRIMARY KEY)"))
            for i in range(14):
                await session.execute(text(f"""CREATE TABLE {SCHEMA}.peer_{i:02}
                    (fk bigint REFERENCES {SCHEMA}.parent(id))"""))
        await self.workspace.set_object_visibility(SCHEMA, "peer_00", "hidden", "fixture")
        await self.workspace.set_object_visibility(SCHEMA, "peer_01", "hidden", "fixture")

        async def incoming():
            async with self.pg.session() as session:
                return await read_lineage_column_references(session, SCHEMA, "parent", "id", "incoming")

        visible = await incoming()
        self.assertFalse(visible.truncated)
        self.assertEqual([node.table for node in visible.references], [f"peer_{i:02}" for i in range(2, 14)])
        await self.workspace.set_object_visibility(SCHEMA, "peer_01", "normal", "fixture")
        capped = await incoming()
        self.assertTrue(capped.truncated)
        self.assertEqual([node.table for node in capped.references], [f"peer_{i:02}" for i in range(1, 13)])

    async def test_lineage_neighbors_keep_native_fk_when_optional_registry_is_absent(self):
        async with self.pg.session() as session:
            await session.execute(text(f"CREATE TABLE {SCHEMA}.parent (id bigint PRIMARY KEY)"))
            await session.execute(text(f"""CREATE TABLE {SCHEMA}.child
                (parent_id bigint REFERENCES {SCHEMA}.parent(id))"""))
            await session.execute(text("DROP TABLE app.ontology_object_links"))
            await session.execute(text("DROP TABLE app.ontology_object_relations"))
        for root, direction, other in (("parent", "incoming", "child"), ("child", "outgoing", "parent")):
            page = await self.layers.lineage_neighbors(SCHEMA, root, direction)
            self.assertEqual(page.model_dump(by_alias=True), {
                "direction": direction, "truncated": False,
                "neighbors": [{"schema": SCHEMA, "table": other, "kinds": ["foreign_key"]}],
            })
        path = await self.layers.lineage_path(SCHEMA, "child", SCHEMA, "parent")
        self.assertEqual(path.state, "found")
        self.assertEqual(path.edges[0].kinds, ["foreign_key"])
        self.assertIsNone((await self.pg.fetch_one(
            "SELECT to_regclass('app.ontology_object_relations') AS registry"))["registry"])

    async def add_lineage_declarations(self, session, edges):
        for start, end in edges:
            await session.execute(text("""
                INSERT INTO app.ontology_object_relations
                    (name, source_schema, source_table, source_fields, target_schema, target_table,
                     target_fields, relationship_kind, cardinality, provenance)
                VALUES (:name, :schema, :start, ARRAY['id'], :schema, :end,
                        ARRAY['id'], 'association', 'many_to_one', 'test')
            """), {"schema": SCHEMA, "start": start, "end": end, "name": f"path_{start}_{end}"})

    async def test_lineage_path_respects_direction_hop_limit_cycles_and_each_node_visibility(self):
        async with self.pg.session() as session:
            for name in ("root", "a", "b", "mid", "tail", "goal", "far"):
                await session.execute(text(f"CREATE TABLE {SCHEMA}.{name} (id bigint PRIMARY KEY)"))
            await session.execute(text(f"ALTER TABLE {SCHEMA}.root ADD FOREIGN KEY (id) REFERENCES {SCHEMA}.a(id)"))
            await self.add_lineage_declarations(session, [
                ("root", "b"), ("root", "a"), ("a", "mid"), ("b", "mid"),
                ("mid", "tail"), ("tail", "root"), ("tail", "goal"), ("goal", "far"),
            ])

        async def path(start, end):
            return await self.layers.lineage_path(SCHEMA, start, SCHEMA, end)

        def destinations(page):
            return [edge.to.table for edge in page.edges]

        earliest = await path("root", "goal")
        self.assertEqual((earliest.state, earliest.searchTruncated, earliest.maxHops), ("found", False, 4))
        self.assertEqual(earliest.model_dump(by_alias=True)["edges"][0], {
            "from": {"schema": SCHEMA, "table": "root"}, "to": {"schema": SCHEMA, "table": "a"},
            "kinds": ["foreign_key", "object_relation"],
        })
        self.assertEqual(destinations(earliest), ["a", "mid", "tail", "goal"])
        self.assertEqual((await path("root", "far")).state, "not_found_within_limit")
        self.assertEqual((await path("goal", "root")).state, "not_found_within_limit")

        await self.workspace.set_object_visibility(SCHEMA, "a", "hidden", "fixture")
        self.assertEqual(destinations(await path("root", "goal")), ["b", "mid", "tail", "goal"])
        await self.workspace.set_object_visibility(SCHEMA, "b", "normal", "fixture")
        await self.pg.execute("""
            UPDATE app.ontology_objects SET is_active=false WHERE schema_name=:schema AND table_name='b'
        """, {"schema": SCHEMA})
        self.assertEqual((await path("root", "goal")).state, "not_found_within_limit")
        self.assertEqual((await self.layers.lineage_neighbors(SCHEMA, "root", "outgoing")).neighbors, [])
        for blocked in ("a", "b", "missing"):
            for start, end in ((blocked, "goal"), ("root", blocked), (blocked, blocked)):
                with self.assertRaises(HTTPException) as error:
                    await path(start, end)
                self.assertEqual((error.exception.status_code, error.exception.detail),
                                 (404, "Table does not exist in this schema"))
        with self.assertRaises(HTTPException) as error:
            await path("root", "root")
        self.assertEqual(error.exception.status_code, 422)

    async def test_common_downstream_returns_two_forward_witnesses_not_a_shared_parent(self):
        async with self.pg.session() as session:
            for name in ("left_end", "right_end", "shared", "tie", "ancestor", "isolated"):
                await session.execute(text(f"CREATE TABLE {SCHEMA}.{name} (id bigint PRIMARY KEY)"))
            await session.execute(text(f"ALTER TABLE {SCHEMA}.right_end ADD FOREIGN KEY (id) REFERENCES {SCHEMA}.shared(id)"))
            await self.add_lineage_declarations(session, [
                ("left_end", "right_end"), ("right_end", "shared"), ("right_end", "tie"),
                ("ancestor", "left_end"), ("ancestor", "right_end"), ("ancestor", "isolated"),
            ])

        async def search(right="right_end"):
            return await self.layers.lineage_common_downstream(SCHEMA, "left_end", SCHEMA, right)

        result = (await search()).model_dump(by_alias=True)
        self.assertEqual((result["state"], result["searchTruncated"], result["descendant"]["table"]),
                         ("found", False, "shared"))
        forward = lambda path: [(step["from"]["table"], step["to"]["table"]) for step in path]
        self.assertEqual(forward(result["leftEdges"]), [("left_end", "right_end"), ("right_end", "shared")])
        self.assertEqual(forward(result["rightEdges"]), [("right_end", "shared")])
        self.assertEqual(result["rightEdges"][0]["kinds"], ["foreign_key", "object_relation"])
        self.assertNotIn("ancestor", result)
        await self.workspace.set_object_visibility(SCHEMA, "shared", "hidden", "fixture")
        self.assertEqual((await search()).descendant.table, "tie")
        await self.workspace.set_object_visibility(SCHEMA, "tie", "hidden", "fixture")
        self.assertEqual((await search()).state, "none_within_limit")
        self.assertEqual((await search("isolated")).state, "none_within_limit")

    async def test_common_upstream_returns_ranked_forward_witnesses_and_keeps_real_root_connector(self):
        async with self.pg.session() as session:
            for name in ("left_end", "right_end", "a_source", "b_source", "downstream", "isolated"):
                await session.execute(text(f"CREATE TABLE {SCHEMA}.{name} (id bigint PRIMARY KEY)"))
            await session.execute(text(f"ALTER TABLE {SCHEMA}.a_source ADD FOREIGN KEY (id) REFERENCES {SCHEMA}.left_end(id)"))
            await self.add_lineage_declarations(session, [
                ("a_source", "left_end"), ("b_source", "left_end"),
                ("left_end", "right_end"), ("left_end", "downstream"), ("right_end", "downstream"),
            ])

        async def search(right="right_end"):
            return await self.layers.lineage_common_upstream(SCHEMA, "left_end", SCHEMA, right)

        result = await search()
        self.assertEqual((result.state, result.searchTruncated, result.ancestor.table), ("found", False, "a_source"))
        self.assertEqual(result.leftEdges[0].kinds, ["foreign_key", "object_relation"])
        self.assertEqual([edge.to.table for edge in result.rightEdges], ["left_end", "right_end"])
        await self.workspace.set_object_visibility(SCHEMA, "a_source", "hidden", "fixture")
        self.assertEqual((await search()).ancestor.table, "b_source")
        await self.workspace.set_object_visibility(SCHEMA, "b_source", "hidden", "fixture")
        self.assertEqual((await search()).state, "none_within_limit")
        self.assertEqual((await search("isolated")).state, "none_within_limit")
        with self.assertRaises(HTTPException) as failure:
            await search("a_source")
        self.assertEqual(failure.exception.status_code, 404)

    async def test_common_upstream_reports_truncated_instead_of_false_absence_or_complete_ranking(self):
        branches = [f"branch_{i:02}" for i in range(13)]
        async with self.pg.session() as session:
            for name in ("left_end", "right_end", *branches):
                await session.execute(text(f"CREATE TABLE {SCHEMA}.{name} (id bigint PRIMARY KEY)"))
            await self.add_lineage_declarations(session, [
                *((name, "left_end") for name in branches), (branches[-1], "right_end"),
            ])

        async def search():
            return await self.layers.lineage_common_upstream(SCHEMA, "left_end", SCHEMA, "right_end")

        result = await search()
        self.assertEqual((result.state, result.searchTruncated, result.ancestor), ("incomplete", True, None))
        async with self.pg.session() as session:
            await self.add_lineage_declarations(session, [(branches[0], "right_end")])
        result = await search()
        self.assertEqual((result.state, result.searchTruncated, result.ancestor.table), ("found", True, branches[0]))

    async def test_lineage_path_reports_partial_search_for_neighbor_and_node_caps(self):
        hops = [f"branch_{i:02}" for i in range(13)]
        children = [f"child_{i:02}" for i in range(24)]
        async with self.pg.session() as session:
            for name in ["root", "goal", "fanout", *hops, *children]:
                await session.execute(text(f"CREATE TABLE {SCHEMA}.{name} (id bigint PRIMARY KEY)"))
            await self.add_lineage_declarations(session, [
                *(("fanout", branch) for branch in hops),
                *(("root", branch) for branch in hops[:12]),
                *((hops[index // 2], child) for index, child in enumerate(children)),
                (children[-1], "goal"),
            ])

        capped = await self.layers.lineage_path(SCHEMA, "fanout", SCHEMA, hops[-1])
        self.assertEqual((capped.state, capped.searchTruncated, capped.edges), ("incomplete", True, []))
        early = await self.layers.lineage_path(SCHEMA, "fanout", SCHEMA, hops[0])
        self.assertEqual((early.state, early.searchTruncated), ("found", True))
        inspected = []
        original = self.layers._lineage_neighbor_rows

        async def track(session, key, *args):
            inspected.append(key)
            return await original(session, key, *args)

        with patch.object(self.layers, "_lineage_neighbor_rows", side_effect=track):
            capped = await self.layers.lineage_path(SCHEMA, "root", SCHEMA, "goal")
        self.assertEqual((capped.state, capped.searchTruncated, capped.edges), ("incomplete", True, []))
        self.assertEqual(len(set(inspected)), 24)
        self.assertNotIn((SCHEMA, children[-1]), inspected)

    async def test_lineage_path_picker_filters_hidden_inactive_and_current_before_its_cap(self):
        names = [f"choice_{i:02}" for i in range(23)]
        async with self.pg.session() as session:
            for name in names:
                await session.execute(text(f"CREATE TABLE {SCHEMA}.{name} (id bigint PRIMARY KEY)"))
            await session.execute(text(f"CREATE VIEW {SCHEMA}.choice_view AS SELECT id FROM {SCHEMA}.choice_00"))
        root = names[-1]
        page = await self.layers.lineage_path_targets(SCHEMA, root, "choice")
        self.assertTrue(page.truncated)
        self.assertEqual([item.table for item in page.items], names[:20])
        await self.workspace.set_object_visibility(SCHEMA, names[0], "hidden", "fixture")
        await self.workspace.set_object_visibility(SCHEMA, names[1], "normal", "fixture")
        await self.pg.execute("""
            UPDATE app.ontology_objects SET is_active=false WHERE schema_name=:schema AND table_name='choice_01'
        """, {"schema": SCHEMA})
        page = await self.layers.lineage_path_targets(SCHEMA, root, f"{SCHEMA.upper()}.CHOICE")
        self.assertFalse(page.truncated)
        self.assertEqual([item.table for item in page.items], names[2:-1])
        empty = await self.layers.lineage_path_targets(SCHEMA, root, "%")
        self.assertEqual((empty.items, empty.truncated), ([], False))
        with self.assertRaises(HTTPException) as error:
            await self.layers.lineage_path_targets(SCHEMA, names[1], "")
        self.assertEqual(error.exception.status_code, 404)

    async def test_lineage_path_timeout_reports_neutral_incomplete_after_endpoint_admission(self):
        async with self.pg.session() as session:
            for name in ("root", "goal"):
                await session.execute(text(f"CREATE TABLE {SCHEMA}.{name} (id bigint PRIMARY KEY)"))

        async def slow(session, *args):
            await session.execute(text("SELECT pg_catalog.pg_sleep(2)"))

        with patch.object(self.layers, "_lineage_neighbor_rows", side_effect=slow):
            page = await self.layers.lineage_path(SCHEMA, "root", SCHEMA, "goal")
        self.assertEqual((page.state, page.searchTruncated, page.edges), ("incomplete", True, []))

    async def test_logical_relation_is_persisted_cross_schema_and_visible_from_both_objects_without_foreign_keys(self):
        payload = await self.relation_payload()
        await self.workspace.create_table(SCHEMA, payload)
        row = await self.pg.fetch_one("SELECT * FROM app.ontology_object_relations WHERE name='firms_company'")
        self.assertEqual((row["source_schema"], row["target_schema"], row["source_fields"], row["target_fields"]),
                         (SCHEMA, TARGET_SCHEMA, ["record_id"], ["record_id"]))
        self.assertEqual((row["provenance"], row["relationship_kind"], row["cardinality"]),
                         ("ontology:create_table", "association", "many_to_one"))
        outgoing = (await self.layers.preview(SCHEMA, "firms"))["linkedObjectTypes"]
        incoming = (await self.layers.preview(TARGET_SCHEMA, "companies"))["linkedObjectTypes"]
        self.assertEqual([(item["id"], item["direction"]) for item in outgoing], [(f"{TARGET_SCHEMA}.companies", "outgoing")])
        self.assertEqual([(item["id"], item["direction"]) for item in incoming], [(f"{SCHEMA}.firms", "incoming")])
        self.assertEqual(outgoing[0]["constraints"], [])
        self.assertEqual(outgoing[0]["relationships"][0]["sourceKind"], "logical")
        constraints = await self.pg.fetch_all("SELECT contype::text AS kind FROM pg_constraint WHERE conrelid=to_regclass(:table)",
                                             {"table": f"{SCHEMA}.firms"})
        self.assertNotIn("f", {item["kind"] for item in constraints})

    async def test_multiple_composite_indexes_are_created_with_distinct_ordered_properties(self):
        payload = deepcopy(PAYLOAD)
        payload["indexes"] = [{"name": "record_name", "fields": ["record_id", "company_name"]},
                              {"name": "name_record", "fields": ["company_name", "record_id"]}]
        await self.workspace.create_table(SCHEMA, payload)
        rows = await self.pg.fetch_all("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname=:schema", {"schema": SCHEMA})
        indexes = {row["indexname"]: row["indexdef"] for row in rows}
        self.assertIn("(record_id, company_name)", indexes["record_name"])
        self.assertIn("(company_name, record_id)", indexes["name_record"])

    async def test_stale_or_unregistered_target_properties_reject_and_roll_back_creation(self):
        payload = await self.relation_payload()
        async with self.pg.session() as session:
            await session.execute(text(f"ALTER TABLE {TARGET_SCHEMA}.companies DROP COLUMN company_name"))
        payload["relations"][0].update(sourceFields=["company_name"], targetFields=["company_name"])
        with self.assertRaises(HTTPException) as error:
            await self.workspace.create_table(SCHEMA, payload)
        self.assertEqual(error.exception.status_code, 422)
        await self.assert_absent("firms")
        self.assertIsNone(await self.pg.fetch_one("SELECT name FROM app.ontology_object_relations WHERE name='firms_company'"))

    async def test_relation_write_failure_rolls_back_table_indexes_properties_and_earlier_relation(self):
        payload = await self.relation_payload()
        payload["relations"].append({**payload["relations"][0], "name": "firms_company_rejected",
                                     "sourceFields": ["company_name"], "targetFields": ["company_name"]})
        async with self.pg.session() as session:
            await session.execute(text(f"""CREATE FUNCTION {SCHEMA}.reject_relation() RETURNS trigger
                LANGUAGE plpgsql AS $$ BEGIN
                RAISE EXCEPTION 'Isolated relation rejection' USING ERRCODE='23514';
                END; $$"""))
            await session.execute(text(f"""CREATE TRIGGER creation_reject_relation BEFORE INSERT
                ON app.ontology_object_relations FOR EACH ROW WHEN (NEW.name='firms_company_rejected')
                EXECUTE FUNCTION {SCHEMA}.reject_relation()"""))
        try:
            with self.assertRaises(HTTPException) as error:
                await self.workspace.create_table(SCHEMA, payload)
            self.assertEqual(error.exception.status_code, 422)
            await self.assert_absent("firms")
            self.assertEqual(await self.pg.fetch_all("SELECT name FROM app.ontology_object_relations WHERE source_schema=:schema",
                                                     {"schema": SCHEMA}), [])
            self.assertIsNone((await self.pg.fetch_one("SELECT to_regclass(:table) AS name",
                                                     {"table": f"{SCHEMA}.company_name_lookup"}))["name"])
        finally:
            async with self.pg.session() as session:
                await session.execute(text("DROP TRIGGER creation_reject_relation ON app.ontology_object_relations"))
                await session.execute(text(f"DROP FUNCTION {SCHEMA}.reject_relation()"))

    async def test_creation_commits_ddl_registration_properties_and_comments(self):
        created = await self.workspace.create_table(SCHEMA, deepcopy(PAYLOAD))
        self.assertEqual((created["name"], created["schemaName"], created["dataType"]), ("firms", SCHEMA, "company"))
        row = await self.metadata()
        self.assertEqual((row["data_type"], row["data_type_source"], row["description"]),
                         ("company", "registration", PAYLOAD["description"]))
        self.assertEqual(row["provenance"], "flow:create_table")
        self.assertTrue(row["is_active"])
        self.assertIn(f'CREATE TABLE "{SCHEMA}"."firms"', row["ddl_sql"])
        columns = await self.pg.fetch_all("""SELECT name, sql_type, ordinal_position, is_nullable,
            is_primary_key, primary_key_position, comment FROM app.ontology_object_properties
            WHERE object_id=:id ORDER BY ordinal_position""", {"id": row["id"]})
        self.assertEqual(columns, [
            {"name": "record_id", "sql_type": "text", "ordinal_position": 1, "is_nullable": False,
             "is_primary_key": True, "primary_key_position": 1, "comment": "Business record identifier"},
            {"name": "company_name", "sql_type": "character varying(160)", "ordinal_position": 2, "is_nullable": False,
             "is_primary_key": False, "primary_key_position": None, "comment": "Registered company name"}])
        physical = await self.pg.fetch_one("SELECT obj_description(to_regclass(:qualified), 'pg_class') AS description",
                                          {"qualified": f"{SCHEMA}.firms"})
        self.assertEqual(physical["description"], PAYLOAD["description"])
        constraints = await self.pg.fetch_all("""SELECT conname, contype::text AS kind FROM pg_constraint
            WHERE conrelid=to_regclass(:qualified) ORDER BY conname""", {"qualified": f"{SCHEMA}.firms"})
        self.assertEqual({row["kind"] for row in constraints}, {"p", "c"})
        self.assertIsNotNone((await self.pg.fetch_one("SELECT to_regclass(:qualified) AS name",
                                                    {"qualified": f"{SCHEMA}.company_name_lookup"}))["name"])

    async def test_new_connection_reads_registered_type_from_catalog_and_preview(self):
        await self.workspace.create_table(SCHEMA, deepcopy(PAYLOAD))
        reader = PostgresClient(URL)
        await reader.connect(max_retries=1)
        layers = object.__new__(EtlLayers)
        layers._pg = reader
        try:
            catalog = await layers.object_types(schema=SCHEMA)
            self.assertEqual(catalog["total"], 1)
            self.assertEqual((catalog["items"][0]["id"], catalog["items"][0]["dataType"], catalog["items"][0]["dataTypeSource"]),
                             (f"{SCHEMA}.firms", "company", "registration"))
            preview = await layers.preview(SCHEMA, "firms")
            self.assertEqual((preview["dataType"], preview["dataTypeSource"], preview["primaryKey"]),
                             ("company", "registration", ["record_id"]))
            self.assertEqual(preview["description"], PAYLOAD["description"])
            self.assertEqual([item["name"] for item in preview["properties"]], ["record_id", "company_name"])
        finally:
            await reader.close()

    async def test_metadata_resync_preserves_explicit_semantic_type(self):
        await self.workspace.create_table(SCHEMA, deepcopy(PAYLOAD))
        before = await self.metadata()
        async with self.pg.session() as session:
            await ontology.sync_object_metadata(
                session, SCHEMA, "firms", catalog_session=session, provenance="isolated:resync",
            )
        after = await self.metadata()
        self.assertEqual((after["id"], after["data_type"], after["data_type_source"]),
                         (before["id"], "company", "registration"))
        self.assertEqual(after["description"], before["description"])
        self.assertEqual((await self.layers.preview(SCHEMA, "firms"))["dataType"], "company")

    async def test_invalid_or_missing_type_is_rejected_before_ddl_or_database_session(self):
        invalid = [{}, *({"dataType": value} for value in (None, "", "Company", " company ", "company-type", 42, True, "x" * 64))]
        with patch.object(self.workspace, "_session", side_effect=AssertionError("Invalid type reached the transaction")) as transaction:
            for position, values in enumerate(invalid):
                with self.subTest(values=values):
                    payload = deepcopy(PAYLOAD)
                    payload.pop("dataType")
                    payload.update(values, name=f"invalid_type_{position}")
                    with self.assertRaises(HTTPException) as raised:
                        await self.workspace.create_table(SCHEMA, payload)
                    self.assertEqual(raised.exception.status_code, 422)
            transaction.assert_not_called()
        for position in range(len(invalid)):
            await self.assert_absent(f"invalid_type_{position}")

    async def test_metadata_database_failure_rolls_back_physical_ddl_and_partial_metadata(self):
        async with self.pg.session() as session:
            await session.execute(text(f"""CREATE FUNCTION {SCHEMA}.reject_property() RETURNS trigger
                LANGUAGE plpgsql AS $$ BEGIN
                RAISE EXCEPTION 'Isolated metadata write rejection' USING ERRCODE='23514';
                END; $$"""))
            await session.execute(text(f"""CREATE TRIGGER creation_reject_property BEFORE INSERT
                ON app.ontology_object_properties FOR EACH ROW WHEN (NEW.name='company_name')
                EXECUTE FUNCTION {SCHEMA}.reject_property()"""))
        try:
            with self.assertRaises(HTTPException) as raised:
                await self.workspace.create_table(SCHEMA, deepcopy(PAYLOAD))
            self.assertEqual(raised.exception.status_code, 422)
            await self.assert_absent("firms")
            self.assertIsNone((await self.pg.fetch_one("SELECT to_regclass(:qualified) AS name",
                                                     {"qualified": f"{SCHEMA}.company_name_lookup"}))["name"])
        finally:
            async with self.pg.session() as session:
                await session.execute(text("DROP TRIGGER creation_reject_property ON app.ontology_object_properties"))
                await session.execute(text(f"DROP FUNCTION {SCHEMA}.reject_property()"))
