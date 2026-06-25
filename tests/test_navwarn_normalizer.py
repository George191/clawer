from __future__ import annotations

from app.etl.normalizers.navwarn import normalize_sealagom_navwarn


def test_navwarn_warning_no_strips_navarea_and_extracts_year() -> None:
    normalized = normalize_sealagom_navwarn(
        {
            "data_source": "sealagom_navwarn",
            "data_type": "navwarn",
            "sea_name": "NAVAREA X",
            "warning_no": "NAVAREA X 068/26",
            "issue_time": "2026-06-24T10:15:00Z",
            "message_text": "AREA BOUNDED BY 10-30.0N 120-15.0E.",
        }
    )

    assert normalized["warning_no"] == "068/26"
    assert normalized["serial_number"] == 68
    assert normalized["warning_year"] == 2026
    assert normalized["region"] == "NAVAREA X"


def test_navwarn_coordinate_is_geography_wkt() -> None:
    normalized = normalize_sealagom_navwarn(
        {
            "data_source": "sealagom_navwarn",
            "data_type": "navwarn",
            "region": "NAVAREA XVIII",
            "warning_no": "45/2026",
            "issue_time": "2026-01-02T00:00:00Z",
            "message_text": "WRECK REPORTED AT 08-15.0S 130-45.0W",
        }
    )

    assert normalized["coordinate"] == "POINT(-130.75 -8.25)"
    assert "latitude" not in normalized
    assert "longitude" not in normalized
    assert "coordinate_count" not in normalized


def test_navwarn_year_serial_warning_no() -> None:
    normalized = normalize_sealagom_navwarn(
        {
            "data_source": "sealagom_navwarn",
            "data_type": "navwarn",
            "region": "NAVAREA XI",
            "warning_no": "26-0267",
            "issue_time": "2026-06-21T00:00:00Z",
            "message_text": "WITHIN 25 MILES OF 31-14-14N 144-26-48E.",
        }
    )

    assert normalized["warning_no"] == "26-0267"
    assert normalized["serial_number"] == 267
    assert normalized["warning_year"] == 2026
    assert normalized["coordinate"] == "POINT(144.446667 31.237222)"
