"""Logical input contracts for the ETL workers that read other layers."""

ODS_SOURCE_LAYERS = ("rds",)
TASK_SOURCE_LAYERS = ("ods",)
DWD_SOURCE_LAYERS = ("ods", "task")

TASK_DATA_TYPES = frozenset({"patent", "vulnerability"})
DWD_DATA_TYPES = frozenset({"patent"})
