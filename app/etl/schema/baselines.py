"""Built-in registry seeds; all DDL is rendered from shared field definitions."""

from app.etl.schema.catalogs import CATALOG_ODS_COLUMNS, current_table_ddl
from app.records.types import SYNC_DATA_TYPES

CANONICAL_ODS_TABLES = frozenset({
    "filing", "filing_document", "financial_fact", "contract", "tender",
    "social_account", "social_content", "social_media", "social_hashtag", "social_interaction",
})
RDS_CURRENT_BASELINE_DDLS = {
    table: current_table_ddl("rds", table) for table in SYNC_DATA_TYPES
}
ODS_CURRENT_BASELINE_DDLS = {
    table: current_table_ddl("ods", table) for table in CATALOG_ODS_COLUMNS
}


def baseline_current_ddls() -> dict[str, dict[str, str]]:
    return {"rds": RDS_CURRENT_BASELINE_DDLS, "ods": ODS_CURRENT_BASELINE_DDLS}
