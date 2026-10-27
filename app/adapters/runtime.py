"""Read source extensions from the existing published adapter artifact."""

from __future__ import annotations

import ast
from time import monotonic
from types import ModuleType

from app.adapters import load_adapter_module_from_source
from app.etl.schema.catalogs import validate_ods_fields, validate_table_name
from app.storage.minio_client import get_business_metadata_minio_client
from app.storage.postgres_client import get_pg_client

_MODULES: dict[str, tuple[float, ModuleType | None]] = {}


def validate_extension_source(source: str) -> None:
    """Validate declarations without running generated code during publication."""
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign | ast.AnnAssign):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id == "ODS_FIELDS":
                    try:
                        fields = ast.literal_eval(node.value)
                    except (ValueError, TypeError) as exc:
                        raise ValueError("ODS_FIELDS must be a literal dictionary") from exc
                    validate_ods_fields(fields)
                elif isinstance(target, ast.Name) and target.id in {"NORMALIZERS", "RECORD_ADAPTERS"}:
                    if not isinstance(node.value, ast.Dict):
                        raise ValueError(f"{target.id} must be a literal dictionary")
                    for key, value in zip(node.value.keys, node.value.values, strict=True):
                        try:
                            validate_table_name(ast.literal_eval(key))
                        except (ValueError, TypeError) as exc:
                            raise ValueError(f"Invalid {target.id} data_type") from exc
                        if not isinstance(value, ast.Name):
                            raise ValueError(f"{target.id} values must name functions/classes")


async def get_source_module(data_source: str) -> ModuleType | None:
    """Use business data_source as-is; aliases resolve artifact ownership only."""
    cached = _MODULES.get(data_source)
    if cached and cached[0] > monotonic():
        return cached[1]
    row = await get_pg_client().fetch_one(
        """
        SELECT name, adapter
        FROM public.ai_collect_templates
        WHERE name = COALESCE(
            (SELECT template_name FROM public.ai_collect_template_alias
             WHERE collection_name = :source LIMIT 1), :source)
        ORDER BY (status = 'active') DESC, updated_at DESC
        LIMIT 1
        """,
        {"source": data_source},
    )
    module = None
    if row and row.get("adapter"):
        key = str(row["adapter"])
        content = await get_business_metadata_minio_client().get_object_bytes(key)
        if content is None:
            raise RuntimeError(f"Published adapter is unavailable: {key}")
        source = content.decode("utf-8")
        validate_extension_source(source)
        module = load_adapter_module_from_source(str(row["name"]), source, key)
        validate_ods_fields(getattr(module, "ODS_FIELDS", {}))
        for export in ("NORMALIZERS", "RECORD_ADAPTERS"):
            mapping = getattr(module, export, {})
            if not isinstance(mapping, dict):
                raise ValueError(f"{export} must be a dictionary in {key}")
            for data_type, fn in mapping.items():
                validate_table_name(data_type)
                if not callable(fn):
                    raise ValueError(f"{export}[{data_type!r}] must be callable in {key}")
    _MODULES[data_source] = (monotonic() + 60, module)
    return module
