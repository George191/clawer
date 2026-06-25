from __future__ import annotations

from app.etl.ts_ods import ODS_NAVWARN_INSERT, _ODS_HISTORY_INSERT_SQL
from app.storage.etl_metadata_store import _ODS_CURRENT_BASELINE_DDLS


def test_navwarn_ddl_uses_geography_coordinate() -> None:
    ddl = _ODS_CURRENT_BASELINE_DDLS["navwarn"]
    assert "coordinate geography(Geometry, 4326)" in ddl
    assert "USING GIST (coordinate)" in ddl
    assert "latitude" not in ddl
    assert "longitude" not in ddl
    assert "coordinate_count" not in ddl
    assert "coordinates JSONB" not in ddl
    assert "sea_name" not in ddl
    assert "warning_prefix" not in ddl


def test_navwarn_insert_sql_uses_st_geog_from_text() -> None:
    assert "ST_GeogFromText(:coordinate)" in ODS_NAVWARN_INSERT
    assert "ST_GeogFromText(:coordinate)" in _ODS_HISTORY_INSERT_SQL["navwarn"]
    assert "latitude" not in ODS_NAVWARN_INSERT
    assert "longitude" not in ODS_NAVWARN_INSERT
    assert "coordinate_count" not in ODS_NAVWARN_INSERT
