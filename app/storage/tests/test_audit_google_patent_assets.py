import pytest
from minio.error import S3Error

from app.storage.audit_google_patent_assets import (
    build_repair_update,
    iter_asset_values,
    iter_expected_asset_paths,
    normalize_object_key,
    object_exists,
)


def test_iter_asset_values_flattens_nested_assets():
    assets = {
        "patent": {
            "pdf": "google_patent/patent/r1/a.pdf",
            "figures": [
                {"full": "google_patent/patent/r1/f1.png", "thumbnail": ""},
            ],
        }
    }

    assert list(iter_asset_values(assets)) == [
        ("assets.patent.pdf", "google_patent/patent/r1/a.pdf"),
        ("assets.patent.figures.0.full", "google_patent/patent/r1/f1.png"),
    ]


def test_normalize_object_key_accepts_key_or_full_minio_url():
    assert normalize_object_key("/google_patent/patent/r1/a.pdf", "bucket") == (
        "google_patent/patent/r1/a.pdf"
    )
    assert normalize_object_key(
        "http://minio.local/bucket/google_patent/patent/r1/a%20b.pdf",
        "bucket",
    ) == "google_patent/patent/r1/a b.pdf"


def test_iter_expected_asset_paths_from_google_patent_source_fields():
    document = {
        "patent": {
            "pdf": "pdf/US1.pdf",
            "thumbnail": "thumb/US1.png",
            "figures": [
                {"full": "figures/US1-1.png", "thumbnail": "thumb/US1-1.png"},
                {"full": ""},
                "figures/US1-3.png",
            ],
        }
    }

    assert list(iter_expected_asset_paths(document)) == [
        "assets.patent.pdf",
        "assets.patent.thumbnail",
        "assets.patent.figures.0.full",
        "assets.patent.figures.0.thumbnail",
        "assets.patent.figures.2",
    ]


def test_build_repair_update_unsets_missing_asset_paths():
    update = build_repair_update({
        "assets.patent.pdf",
        "assets.patent.figures.0.full",
    })

    assert update["$set"]["_meta.download_status"] == "pending"
    assert update["$set"]["_meta.sync_status"] == "pending"
    assert update["$unset"]["_meta.download_claim_token"] == ""
    assert update["$unset"]["assets.patent.pdf"] == ""
    assert update["$unset"]["assets.patent.figures.0.full"] == ""


class FakeMinio:
    def __init__(self, error_code=None):
        self.error_code = error_code

    def stat_object(self, bucket, object_key):
        if self.error_code:
            raise S3Error(None, self.error_code, "message", object_key, "req", "host")


@pytest.mark.asyncio
async def test_object_exists_treats_only_missing_codes_as_missing():
    assert await object_exists(FakeMinio(), "bucket", "exists.pdf") is True
    assert await object_exists(FakeMinio("NoSuchKey"), "bucket", "missing.pdf") is False

    with pytest.raises(S3Error):
        await object_exists(FakeMinio("AccessDenied"), "bucket", "blocked.pdf")
