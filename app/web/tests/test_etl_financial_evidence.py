"""Table evidence uses catalog metadata; financial ODS requires an indexed observation."""

import json
from datetime import date, datetime, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.web.api.dependencies.common import get_current_user
from app.web.api.routes import etl
from app.web.tests.test_etl_object_types import Result, service


def index_plan(**changes):
    return {"Node Type": "Index Only Scan", "Index Name": "facts_filed_idx",
            "Index Cond": "(filed IS NOT NULL)", **changes}


def explain(child):
    return [{"Plan": {"Node Type": "Limit", "Plan Rows": 1, "Plans": [child]}}]


def evidence_service(*results):
    instance, sql = service(Result(), *results)
    sql.allowed_prefixes = ("SET LOCAL statement_timeout = '2s'", "SELECT", "EXPLAIN (FORMAT JSON)")
    return instance, sql


def target(*, filed_is_date=True):
    return Result([{"filed_is_date": filed_is_date, "column_count": 9,
                    "primary_key_column_count": 2, "not_null_column_count": 5}])


class FinancialEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_ordered_merge_index_plan_reads_only_latest_business_date(self):
        plan = explain({"Node Type": "Merge Append", "Plans": [index_plan(), index_plan()]})
        instance, sql = evidence_service(target(), Result(scalar=json.dumps(plan)),
                                         Result(scalar=date(2026, 10, 31)))
        result = await instance.evidence("ts_ods", "ods_financial_fact")
        self.assertEqual(result["latestFiledDate"], "2026-10-31")
        self.assertEqual(result["observation"], "observed")
        self.assertEqual(datetime.fromisoformat(result["observedAt"]).utcoffset(), timedelta(0))
        self.assertEqual(result["history"], {"status": "unavailable"})
        self.assertEqual(result["quality"], {"source": "postgres_catalog", "columnCount": 9,
                                             "primaryKeyColumnCount": 2, "notNullColumnCount": 5})
        self.assertEqual(result["lineage"], {
            "status": "declared",
            "mongoCollection": "financial_fact_sec_edgar",
            "rdsTable": "ts_rds.rds_financial_fact",
            "odsTable": "ts_ods.ods_financial_fact",
        })
        business = "SELECT filed FROM ts_ods.ods_financial_fact WHERE filed IS NOT NULL ORDER BY filed DESC LIMIT 1"
        self.assertEqual([statement for statement, _ in sql.calls[-2:]], [
            "EXPLAIN (FORMAT JSON) " + business, business,
        ])

    async def test_unsafe_or_unrecognized_plans_never_run_the_business_select(self):
        cases = [
            explain({"Node Type": "Seq Scan"}),
            explain({"Node Type": "Sort", "Plans": [index_plan()]}),
            explain({"Node Type": "Bitmap Heap Scan"}),
            explain(index_plan(Filter="(filed IS NOT NULL)")),
            explain(index_plan(**{"Index Cond": "(cik IS NOT NULL)"})),
            explain({"Node Type": "Merge Append", "Plans": [index_plan()] * 129}),
            explain({"Node Type": "Merge Append", "Plans": [index_plan(), {"Node Type": "Seq Scan"}]}),
            "malformed",
        ]
        for plan in cases:
            with self.subTest(plan=plan):
                instance, sql = evidence_service(target(), Result(scalar=plan))
                result = await instance.evidence("ods", "ods_financial_fact")
                self.assertEqual(result["observation"], "safe_plan_unavailable")
                self.assertIsNone(result["latestFiledDate"])
                self.assertTrue(sql.calls[-1][0].startswith("EXPLAIN (FORMAT JSON)"))
                self.assertFalse(any(statement.startswith("SELECT filed") for statement, _ in sql.calls))

    async def test_no_nonnull_date_and_missing_or_changed_column_are_distinct(self):
        instance, _ = evidence_service(target(), Result(scalar=explain(index_plan())),
                                       Result(scalar=None))
        result = await instance.evidence("ods", "ods_financial_fact")
        self.assertEqual(result["observation"], "no_filed_date")
        self.assertIsNone(result["latestFiledDate"])

        instance, sql = evidence_service(target(filed_is_date=False))
        result = await instance.evidence("ods", "ods_financial_fact")
        self.assertEqual(result["observation"], "safe_plan_unavailable")
        self.assertFalse(any(statement.startswith("EXPLAIN") for statement, _ in sql.calls))

    async def test_generic_evidence_stops_at_physical_metadata_even_when_filed_exists(self):
        instance, sql = evidence_service(target())
        result = await instance.evidence("ts_rds", "rds_financial_fact")
        self.assertEqual(result["observation"], "metadata_only")
        self.assertIsNone(result["latestFiledDate"])
        self.assertEqual(result["history"], {"status": "unavailable"})
        self.assertEqual(result["lineage"], {"status": "unavailable"})
        self.assertEqual(result["quality"], {"source": "postgres_catalog", "columnCount": 9,
                                             "primaryKeyColumnCount": 2, "notNullColumnCount": 5})
        self.assertEqual(len(sql.calls), 2)  # Business timeout and physical observation; readiness is checked in the system DB.
        statement, params = sql.calls[-1]
        self.assertIn("FROM pg_class", statement)
        self.assertIn("FROM pg_attribute", statement)
        self.assertIn("LEFT JOIN pg_index", statement)
        self.assertIn("NOT a.attisdropped", statement)
        self.assertIn("NOT c.relispartition", statement)
        self.assertEqual(params["table"], "rds_financial_fact")
        self.assertNotIn("rds_financial_fact", statement)

    async def test_metadata_schema_or_invalid_identifier_never_opens_a_database_session(self):
        instance, sql = evidence_service()
        for schema, table, status in (("ts_meta", "docs", 404), ("ts_ods", "malformed;name", 400)):
            with self.subTest(schema=schema), self.assertRaises(HTTPException) as raised:
                await instance.evidence(schema, table)
            self.assertEqual(raised.exception.status_code, status)
        self.assertFalse(sql.calls)


class FinancialEvidenceRouteTests(unittest.TestCase):
    path = "/api/v1/etl/ts_ods/ods_financial_fact/evidence"

    def setUp(self):
        self.app = FastAPI()
        self.app.include_router(etl.router, prefix="/api/v1")
        self.client = TestClient(self.app)

    def test_hidden_and_missing_table_use_same_404_for_non_admin(self):
        instance, sql = evidence_service(Result([]))
        self.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(is_superuser=False)
        with patch.object(etl, "get_etl_layers", return_value=instance):
            response = self.client.get(self.path + "?includeHidden=true")
        self.assertEqual(response.status_code, 404)
        statement, params = sql.calls[-1]
        self.assertIn("COALESCE(o.visibility, 'normal') <> 'hidden'", statement)
        self.assertFalse(params["include_hidden"])

    def test_admin_may_include_existing_hidden_table(self):
        instance, sql = evidence_service(target(filed_is_date=False))
        self.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(is_superuser=True)
        with patch.object(etl, "get_etl_layers", return_value=instance):
            response = self.client.get(self.path)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(sql.calls[-1][1]["include_hidden"])

    def test_normal_nonfinance_table_returns_only_generic_evidence(self):
        instance, sql = evidence_service(target(filed_is_date=False))
        self.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(is_superuser=False)
        with patch.object(etl, "get_etl_layers", return_value=instance):
            response = self.client.get("/api/v1/etl/ts_ods/ods_filing/evidence")
            removed = self.client.get("/api/v1/etl/ts_ods/ods_financial_fact/financial-evidence")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["lineage"], {"status": "unavailable"})
        self.assertEqual(response.json()["observation"], "metadata_only")
        self.assertEqual(len(sql.calls), 2)
        self.assertEqual(removed.status_code, 404)

    def test_authentication_failure_prevents_every_evidence_read(self):
        def reject():
            raise HTTPException(401, "Authentication required")

        self.app.dependency_overrides[get_current_user] = reject
        with patch.object(etl, "get_etl_layers") as layers:
            response = self.client.get(self.path)
        self.assertEqual(response.status_code, 401)
        layers.assert_not_called()
