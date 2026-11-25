"""Validate table controls without a database or business-record reads."""

import unittest
from copy import deepcopy

from fastapi import HTTPException

from app.web.services.etl_workspace import build_table_statements, table_relations

PAYLOAD = {"name": "filings", "fields": [
    {"name": "id", "type": "BIGINT", "primary": True},
    {"name": "cik", "type": "TEXT"}, {"name": "year", "type": "INTEGER"}]}
RELATION = {"name": "filing_company", "targetSchema": "ts_companies", "targetTable": "companies",
    "sourceFields": ["cik"], "targetFields": ["cik"], "cardinality": "many_to_one", "description": "Issuer"}


class TableDefinitionTests(unittest.TestCase):
    def assert_invalid_index(self, indexes, message):
        with self.assertRaises(HTTPException) as error:
            build_table_statements("ts_filings", {**PAYLOAD, "indexes": indexes})
        self.assertEqual(error.exception.status_code, 422)
        self.assertIn(message, error.exception.detail)

    def test_multiple_composite_indexes_preserve_property_order(self):
        statements = build_table_statements("ts_filings", {**PAYLOAD, "indexes": [
            {"name": "issuer_year", "type": "BTREE", "fields": ["cik", "year"]},
            {"name": "year_issuer", "type": "BTREE", "fields": "year,cik"},
            {"name": "issuer_year_unique", "type": "BTREE", "fields": ["cik", "year"], "unique": True}]})
        self.assertEqual(statements[1:], [
            'CREATE INDEX "issuer_year" ON "ts_filings"."filings" USING BTREE ("cik", "year")',
            'CREATE INDEX "year_issuer" ON "ts_filings"."filings" USING BTREE ("year", "cik")',
            'CREATE UNIQUE INDEX "issuer_year_unique" ON "ts_filings"."filings" USING BTREE ("cik", "year")'])

    def test_index_validation_rejects_bad_names_properties_methods_and_duplicates(self):
        cases = [
            ([{"name": "same", "fields": "cik"}, {"name": "same", "fields": "year"}], "names must be distinct"),
            ([{"name": "bad", "fields": ["missing"]}], "Unknown column"),
            ([{"name": "bad", "fields": ["cik", "cik"]}], "distinct columns"),
            ([{"name": "bad", "fields": []}], "distinct columns"),
            ([{"name": "bad", "fields": "cik", "type": "INVALID"}], "Unsupported index method"),
            ([{"name": "bad", "fields": "cik", "type": "HASH", "unique": True}], "Unique indexes require BTREE"),
            ([{"name": "bad", "fields": ["cik", "year"], "type": "HASH"}], "exactly one property"),
            ([{"name": "one", "fields": ["cik", "year"]},
              {"name": "two", "fields": "cik,year", "type": "btree", "unique": False}], "Duplicate index definition")]
        for indexes, message in cases:
            with self.subTest(indexes=indexes):
                self.assert_invalid_index(indexes, message)

    def test_single_property_hash_index_remains_valid(self):
        statements = build_table_statements("ts_filings", {**PAYLOAD, "indexes": [
            {"name": "issuer_hash", "type": "HASH", "fields": ["cik"]}]})
        self.assertIn('USING HASH ("cik")', statements[1])

    def test_physical_foreign_keys_are_rejected_explicitly(self):
        for foreign_keys in ([], [{"name": "old_fk", "fields": "cik", "referenceTable": "companies"}]):
            with self.subTest(foreign_keys=foreign_keys), self.assertRaises(HTTPException) as error:
                build_table_statements("ts_filings", {**PAYLOAD, "foreignKeys": foreign_keys})
            self.assertEqual(error.exception.status_code, 422)
            self.assertIn("Physical foreign keys are not supported", error.exception.detail)

    def test_logical_relation_uses_current_source_and_canonical_metadata(self):
        row, = table_relations("ts_filings", {**PAYLOAD, "relations": [RELATION]})
        self.assertEqual((row["source_schema"], row["source_table"], row["source_fields"]),
                         ("ts_filings", "filings", ["cik"]))
        self.assertEqual((row["target_schema"], row["target_table"], row["target_fields"]),
                         ("ts_companies", "companies", ["cik"]))
        self.assertEqual((row["relationship_kind"], row["provenance"], row["enabled"]),
                         ("association", "ontology:create_table", True))
        self.assertFalse(any("FOREIGN KEY" in sql for sql in build_table_statements(
            "ts_filings", {**PAYLOAD, "relations": [RELATION]})))

    def test_invalid_relation_shapes_sources_targets_and_cardinalities_are_rejected(self):
        invalid = [None, {}, [None], [{**RELATION, "sourceSchema": "ts_override"}],
            [{**RELATION, "sourceFields": ["unknown"]}], [{**RELATION, "targetSchema": "ts_meta"}],
            [{**RELATION, "targetSchema": "ts_meta_hist"}], [{**RELATION, "targetSchema": "information_schema"}],
            [{**RELATION, "targetFields": []}], [{**RELATION, "targetFields": ["cik", "year"]}],
            [{**RELATION, "sourceFields": ["cik", "cik"], "targetFields": ["id", "cik"]}],
            [{**RELATION, "name": "bad\nname"}], [{**RELATION, "cardinality": "invalid"}],
            [{**RELATION, "description": "x" * 4001}], [RELATION] * 201]
        for relations in invalid:
            with self.subTest(relations=relations), self.assertRaises(HTTPException) as error:
                table_relations("ts_filings", {**PAYLOAD, "relations": relations})
            self.assertEqual(error.exception.status_code, 422)

    def test_duplicate_names_or_reordered_property_pairs_are_rejected(self):
        for rows in ([RELATION, RELATION], [
            {**RELATION, "sourceFields": ["cik", "year"], "targetFields": ["cik", "year"]},
            {**RELATION, "name": "same_mapping", "sourceFields": ["year", "cik"], "targetFields": ["year", "cik"]}]):
            with self.subTest(rows=rows), self.assertRaises(HTTPException) as error:
                table_relations("ts_filings", {**PAYLOAD, "relations": deepcopy(rows)})
            self.assertEqual(error.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
