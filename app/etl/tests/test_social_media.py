from app.etl.normalizers.social_media import normalize_twitter_media
from app.etl.normalizers.social_identity import twitter_record_id
from app.etl.schema.catalogs import CATALOG_ODS_COLUMNS


def test_media_id_column_removed_without_changing_record_identity():
    source = 'https://pbs.twimg.com/media/photo.jpg'
    for media_key in (None, 'platform-key'):
        record = normalize_twitter_media({
            'id': '300', 'media': [{'type': 'image', 'url': source, 'media_key': media_key}],
            'assets': {'media': [{'url': 'tweet/twitter/record/photo.jpg'}]},
        })[0]
        assert 'platform_media_id' not in record
        assert 'platform_media_id' not in CATALOG_ODS_COLUMNS['social_media']
        assert record['source_url'] == source
        assert record['url'] == 'tweet/twitter/record/photo.jpg'
        assert record['record_id'] == twitter_record_id('media', {
            'content_id': '300', 'media_id': media_key or source})
