import pytest

from tools.migrate_twitter_prefixes import media_alias_updates


def test_identical_media_alias_is_removed_without_touching_other_values():
    record = {'media': [{'contentType': 'video/mp4', 'content_type': 'video/mp4'}],
              'other': {'contentType': 'keep'},
              '_meta': {'media': {'contentType': 'keep', 'content_type': 'keep'}}}
    assert media_alias_updates(record) == {'media.0.contentType': ''}
    assert record['media'][0]['contentType'] == 'video/mp4'


def test_conflicting_media_aliases_are_not_discarded():
    with pytest.raises(ValueError, match='Conflicting media content types'):
        media_alias_updates({'media': [{'contentType': 'video/mp4', 'content_type': 'image/jpeg'}]})


def test_nested_tweet_media_aliases_are_included():
    record = {'referenced_tweets': [{'tweet': {'media': [
        {'contentType': 'video/mp4', 'content_type': 'video/mp4'},
    ]}}]}
    assert media_alias_updates(record) == {'referenced_tweets.0.tweet.media.0.contentType': ''}
