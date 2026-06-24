from datetime import datetime, timezone

from app.etl.normalizers.intelligence import normalize_planet
from app.etl.normalizers.patent import normalize_google_patent


def test_normalize_intelligence_prefers_downloaded_asset_path_and_parses_modified_timestamp() -> None:
    record = {
        "_meta": {
            "template": "planet",
            "data_source": "planet",
            "record_id": "intel-1",
            "download_status": "downloaded",
            "search_params": {"domain": "4589"},
        },
        "title": "Edge",
        "url": "https://planet4589.org/space/papers/Edge.pdf",
        "name": "Edge.pdf",
        "summary": "summary",
        "modified": "11/20/2018 03:15:46 AM +00:00",
        "assets": {
            "url": "intelligence/planet/intel-1/Edge.pdf",
        },
        "category": "history",
    }

    normalized = normalize_planet(record)

    assert normalized["url"] == "intelligence/planet/intel-1/Edge.pdf"
    assert normalized["source_updated_at"] == datetime(2018, 11, 20, 3, 15, 46, tzinfo=timezone.utc)
    assert "original_file" not in normalized
    assert "extra_data" not in normalized


def test_normalize_patent_prefers_downloaded_asset_paths() -> None:
    record = {
        "_meta": {
            "template": "google_patent",
            "data_source": "google_patent",
            "record_id": "patent-1",
        },
        "patent": {
            "title": "Example patent",
            "publication_number": "US-1",
            "assignee": "Example Corp",
            "abstract": "abstract",
            "pdf": "https://patents.example.com/original.pdf",
            "thumbnail": "https://patents.example.com/thumb.png",
        },
        "assets": {
            "pdf": "patent/google_patent/patent-1/original.pdf",
            "thumbnail": "patent/google_patent/patent-1/thumb.png",
        },
    }

    normalized = normalize_google_patent(record)

    assert normalized["url"] == "patent/google_patent/patent-1/original.pdf"
    assert normalized["thumbnail"] == "patent/google_patent/patent-1/thumb.png"
    assert "original_file" not in normalized
    assert "extra_data" not in normalized
