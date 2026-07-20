from pathlib import Path

import pytest

from app.web.routes.ai_collect import (
    _detect_page_warnings,
    _validate_generated_template,
)


def test_detects_dynamic_shell_and_failed_html_api() -> None:
    warnings = _detect_page_warnings(
        "<table><tr><td>Loading, Please Wait...</td></tr></table>",
        [
            {
                "url": "https://msi.nga.mil/api/publications/smaps?navArea=4",
                "status": 503,
                "contentType": "text/html",
                "resourceType": "fetch",
            }
        ],
    )

    assert any("dynamic loading shell" in warning for warning in warnings)
    assert any("Dynamic data requests failed" in warning for warning in warnings)
    assert any("returned HTML" in warning for warning in warnings)


def test_detects_maintenance_page() -> None:
    warnings = _detect_page_warnings(
        "<h1>MSI IS CURRENTLY UNDER MAINTENANCE</h1>",
        [],
    )

    assert warnings == ["The captured page is a maintenance or service-unavailable response."]


def test_generated_template_validation_preserves_preflight_warnings() -> None:
    template_yaml = Path("templates/nga_navwarn.yaml").read_text(encoding="utf-8")

    template, warnings = _validate_generated_template(
        template_yaml,
        "https://msi.nga.mil/queryResults?publications/smaps",
        ["Dynamic data requests failed: 503 SMAPS"],
    )

    assert template["list_page"].startswith("/api/publications/smaps")
    assert warnings == ["Dynamic data requests failed: 503 SMAPS"]


def test_generated_template_validation_rejects_invalid_yaml() -> None:
    with pytest.raises(RuntimeError, match="invalid YAML"):
        _validate_generated_template("name: [", "https://example.com", [])


def test_generated_template_validation_checks_contract_fields() -> None:
    template_yaml = """
name: sample
base_url: https://example.com
data_type: news
response_type: json
list_page: /api/news
list_fields:
  - name: title
    selector: title
    selector_type: json
dedup_fields: [missing_id]
download:
  - selector: attachments
    selector_type: json
    link_type: href
    asset_type: attachment
"""

    _, warnings = _validate_generated_template(
        template_yaml,
        "https://example.com/news",
        [],
        {
            "source_kind": "api",
            "selected_endpoint": "https://example.com/api/articles",
        },
    )

    assert any("Dedup fields are not produced" in warning for warning in warnings)
    assert any("Resource selectors require adapter output" in warning for warning in warnings)
    assert any("does not target the API" in warning for warning in warnings)
