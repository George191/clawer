import unittest
from unittest.mock import AsyncMock, Mock

from app.storage.postgres_client import PostgresClient

FACT_TABLE = "ts_rds.rds_financial_fact"
FACT_INDEX = "idx_rds_financial_fact_cik"
FACT_INDEX_DDL = f"CREATE INDEX IF NOT EXISTS {FACT_INDEX} ON {FACT_TABLE} ((raw_data->>'cik'))"


class SchemaSession:
    def __init__(self, *, indexes=(), table_schemas=None):
        self.indexes = set(indexes)
        self.table_schemas = table_schemas or {FACT_TABLE: "ts_rds"}
        self.ddl = []
        self.lookups = []
        self.execute = AsyncMock(side_effect=self._execute)
        self.commit = AsyncMock()
        self.rollback = AsyncMock()
        self.close = AsyncMock()

    async def _execute(self, statement, params=None):
        sql = str(statement).strip()
        result = Mock()
        if sql.upper().startswith("SELECT"):
            self.lookups.append(params)
            schema = self.table_schemas[params["table_name"]]
            exists = (schema, params["index_name"]) in self.indexes
            result.scalar_one_or_none.return_value = 1 if exists else None
        else:
            self.ddl.append(sql)
        return result


class PostgresSchemaInitTests(unittest.IsolatedAsyncioTestCase):
    def client(self, session):
        client = PostgresClient()
        client.connect = AsyncMock()
        client._session_factory = Mock(return_value=session)
        return client

    async def test_existing_fact_index_never_executes_locking_ddl(self):
        session = SchemaSession(indexes={("ts_rds", FACT_INDEX)})
        await self.client(session).init_schema([FACT_INDEX_DDL])
        self.assertEqual(session.ddl, [])
        self.assertEqual(
            session.lookups,
            [
                {"table_name": FACT_TABLE, "index_name": FACT_INDEX},
            ],
        )
        session.commit.assert_awaited_once()
        session.rollback.assert_not_awaited()

    async def test_missing_index_still_executes_original_ddl(self):
        session = SchemaSession()
        await self.client(session).init_schema([FACT_INDEX_DDL])
        self.assertEqual(session.ddl, [FACT_INDEX_DDL])

    async def test_index_with_same_name_in_other_schema_does_not_skip(self):
        session = SchemaSession(indexes={("public", FACT_INDEX)})
        await self.client(session).init_schema([FACT_INDEX_DDL])
        self.assertEqual(session.ddl, [FACT_INDEX_DDL])
        self.assertEqual(session.lookups[0]["table_name"], FACT_TABLE)

    async def test_other_statements_and_unconditional_indexes_are_unchanged(self):
        statements = [
            "CREATE SCHEMA IF NOT EXISTS ts_rds",
            f"CREATE INDEX {FACT_INDEX} ON {FACT_TABLE} (record_id)",
            f"ALTER TABLE {FACT_TABLE} ADD COLUMN example TEXT",
        ]
        session = SchemaSession(indexes={("ts_rds", FACT_INDEX)})
        await self.client(session).init_schema(statements)
        self.assertEqual(session.ddl, statements)
        self.assertEqual(session.lookups, [])

    async def test_skipping_one_index_preserves_other_ddl_in_same_block(self):
        create_table = f"CREATE TABLE IF NOT EXISTS {FACT_TABLE} (record_id TEXT)"
        missing_index = f"CREATE INDEX IF NOT EXISTS idx_fact_record ON {FACT_TABLE} (record_id)"
        alter_table = f"ALTER TABLE {FACT_TABLE} ADD COLUMN example TEXT"
        block = ";\n".join([create_table, FACT_INDEX_DDL, missing_index, alter_table])
        session = SchemaSession(indexes={("ts_rds", FACT_INDEX)})
        await self.client(session).init_schema([block])
        self.assertEqual(session.ddl, [create_table, missing_index, alter_table])
        session.commit.assert_awaited_once()

    async def test_quoted_unique_index_and_only_table_preserve_case(self):
        table = '"Ts_Rds"."Rds_FinancialFact"'
        statement = f'CREATE UNIQUE INDEX IF NOT EXISTS "FactCik" ON ONLY {table} (record_id)'
        session = SchemaSession(
            indexes={("Ts_Rds", "FactCik")},
            table_schemas={table: "Ts_Rds"},
        )
        await self.client(session).init_schema([statement])
        self.assertEqual(session.ddl, [])
        self.assertEqual(
            session.lookups,
            [
                {"table_name": table, "index_name": "FactCik"},
            ],
        )

    async def test_unquoted_uppercase_index_resolves_lowercase_name(self):
        table = "TS_RDS.RDS_FINANCIAL_FACT"
        statement = f"create index if not exists IDX_RDS_FINANCIAL_FACT_CIK on {table} (record_id)"
        session = SchemaSession(
            indexes={("ts_rds", FACT_INDEX)},
            table_schemas={table: "ts_rds"},
        )
        await self.client(session).init_schema([statement])
        self.assertEqual(session.ddl, [])
        self.assertEqual(session.lookups[0]["index_name"], FACT_INDEX)

    async def test_escaped_quote_in_index_name_is_passed_as_identifier_value(self):
        statement = f'CREATE INDEX IF NOT EXISTS "fact""cik" ON {FACT_TABLE} (record_id)'
        session = SchemaSession(indexes={("ts_rds", 'fact"cik')})
        await self.client(session).init_schema([statement])
        self.assertEqual(session.ddl, [])
        self.assertEqual(session.lookups[0]["index_name"], 'fact"cik')


if __name__ == "__main__":
    unittest.main()
