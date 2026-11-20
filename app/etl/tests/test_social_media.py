import json

import pytest

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

@pytest.mark.parametrize('thumbnail_asset,expected', [
    ('tweet/twitter/record/cover.jpg', 'tweet/twitter/record/cover.jpg'),
    (None, None),
    ('https://pbs.twimg.com/cover.jpg', None),
    ({'status_code': 404, 'url': ''}, None),
])
def test_video_thumbnail_preserves_downloaded_path_and_source(thumbnail_asset, expected):
    thumbnail = 'https://pbs.twimg.com/cover.jpg'
    media = {'type': 'video', 'url': 'https://video.twimg.com/video.mp4',
             'thumbnail_url': thumbnail, 'duration': 34328}
    asset = {'url': 'tweet/twitter/record/video.mp4'}
    if thumbnail_asset is not None:
        asset['thumbnail_url'] = thumbnail_asset
    tweet = {'id': '300', 'media': [media], 'assets': {'media': [asset]}}

    record = normalize_twitter_media(tweet)[0]

    assert record['thumbnail'] == expected
    assert CATALOG_ODS_COLUMNS['social_media']['thumbnail'] == 'TEXT'
    assert record['url'] == asset['url']
    assert record['source_url'] == media['url']
    assert json.loads(record['extra'])['source_media'] == media
    assert tweet['media'][0]['thumbnail_url'] == thumbnail


def test_thumbnail_asset_indexes_do_not_shift_when_an_image_has_no_thumbnail():
    records = normalize_twitter_media({
        'id': '300',
        'media': [{'type': 'image', 'url': 'https://pbs.twimg.com/photo.jpg'},
                  {'type': 'video', 'url': 'https://video.twimg.com/video.mp4',
                   'thumbnail_url': 'https://pbs.twimg.com/cover.jpg'}],
        'assets': {'media': [None, {'thumbnail_url': 'tweet/twitter/record/cover.jpg'}]},
    })
    assert [record['thumbnail'] for record in records] == [None, 'tweet/twitter/record/cover.jpg']
