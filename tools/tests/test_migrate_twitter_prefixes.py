import pytest
from copy import deepcopy
from datetime import datetime, timezone
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from contextlib import nullcontext

from tools.migrate_twitter_prefixes import media_alias_updates
from tools import migrate_download_paths as migration
from tools import migrate_twitter_prefixes as scoped


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


def test_native_media_type_without_canonical_field_is_selected_for_repair():
    assert media_alias_updates({'media': [{'contentType': 'video/mp4'}]}) == {'media.0.contentType': ''}


@pytest.mark.parametrize('changed', [None, {'write_token': 'stale'}, {'data_type': 'social_media'},
                                    {'template': 'wrong'}, {'download_version': 1}])
async def test_verification_requires_the_current_source_contract(tmp_path, changed):
    record_id = 'a' * 32
    old, new = f'tweet/twitter/{record_id}/old.mp4', f'tweet/twitter/{record_id}/' + 'b' * 64 + '.mp4'
    db = migration.journal(tmp_path / 'journal.sqlite')
    db.execute('INSERT INTO objects(old,prefix,record_id,shard,new,phase) VALUES(?,?,?,?,?,?)',
               (old, 'tweet/twitter', record_id, 0, new, 'source_updated'))
    db.commit()
    route = {'collection_name': 'tweet_twitter', 'template_name': 'twitter', 'data_type': 'tweet'}
    doc = {'_meta': {'updated_at': datetime(2026, 11, 20, tzinfo=timezone.utc), 'download_version': 2,
                     'write_token': 'current', 'data_type': 'tweet', 'template': 'tweet_twitter'},
           'assets': {'media': {'0': {'url': new}}}}
    snapshot = deepcopy(doc)
    if changed:
        snapshot['_meta'].update(changed)
    pg = SimpleNamespace(connection_id='warehouse', revision=1, fetch_all=AsyncMock(side_effect=[
        [{'raw_data': snapshot}], [{'row': {'url': new}}],
    ]))
    module = SimpleNamespace(NORMALIZERS={'tweet': lambda raw: {
        'data_type': 'social_media', 'record_id': record_id, 'url': new}})
    runtime = SimpleNamespace(get_source_module=AsyncMock(return_value=module))
    client = Mock()
    with patch.object(migration, 'source_documents', return_value=[(route, doc)]), \
            patch.object(migration.business_database, 'get_layer_pg_client', return_value=pg), \
            patch.object(migration.business_database, 'layer_binding_lease', return_value=nullcontext()), \
            patch('app.etl.schema.registry.require_registered_object', new=AsyncMock(side_effect=lambda _, layer, kind, role:
                         {'schema_name':'warehouse.' + layer, 'table_name':'current_' + kind})), \
            patch.dict(sys.modules, {'app.adapters.runtime': runtime}):
        result = await migration.verify_group(db, client, None, [], 'tweet/twitter', record_id)
    assert result is (changed is None)
    if changed:
        runtime.get_source_module.assert_not_called()
        client.stat_object.assert_not_called()
    else:
        assert db.execute('SELECT phase FROM objects').fetchone()[0] == 'verified'
        assert '"warehouse.rds"."current_tweet"' in pg.fetch_all.await_args_list[0].args[0]
        assert '"warehouse.ods"."current_social_media"' in pg.fetch_all.await_args_list[1].args[0]
    db.close()


def test_busy_source_waits_without_stopping_other_work_or_falsely_completing(tmp_path):
    record_id = 'a' * 32
    old, new = f'tweet/twitter/{record_id}/old.mp4', f'tweet/twitter/{record_id}/' + 'b' * 64 + '.mp4'
    path = tmp_path / 'journal.sqlite'
    db = migration.journal(path)
    db.execute('INSERT INTO progress VALUES(?,?,1)', ('twitter-contract-assets:0', ''))
    db.execute('INSERT INTO objects(old,prefix,record_id,shard,new) VALUES(?,?,?,?,?)',
               (old, 'tweet/twitter', record_id, 0, new))
    db.commit()
    route = {'collection_name': 'tweet_twitter', 'template_name': 'twitter', 'data_type': 'tweet'}
    busy = {'_id': 1, '_meta': {'download_status': 'downloading'}, 'assets': {'file': old}}
    ready = {'_id': 1, '_meta': {'download_status': 'downloaded', 'data_type': 'tweet',
                               'template': 'tweet_twitter'}, 'assets': {'file': new}}
    collection = Mock()
    collection.find_one.side_effect = [{'_meta': {'download_status': 'downloading'}},
                                       {'_meta': {'download_status': 'downloaded'}}]
    mongo_client = Mock()
    mongo_client.__getitem__ = Mock(return_value={'tweet_twitter': collection})
    storage = Mock()
    async def verify(journal, *args):
        journal.execute('UPDATE objects SET phase="verified" WHERE old=?', (old,))
        journal.commit()
        return True
    repair = AsyncMock()
    with patch.object(migration, 'storage', return_value=storage), \
            patch.object(migration, 'routes', AsyncMock(return_value=[route])), \
            patch.object(scoped, 'MongoClient', return_value=mongo_client), \
            patch.object(migration, 'source_documents', side_effect=[[(route, busy)], [(route, ready)], [(route, ready)]]), \
            patch.object(migration, 'connect_pg', AsyncMock(return_value=SimpleNamespace(close=AsyncMock()))), \
            patch.object(migration, 'verify_group', verify), \
            patch.object(migration, 'repair_references', repair), \
            patch.object(scoped.asyncio, 'sleep', AsyncMock()) as sleep:
        scoped.worker(str(path), 0, nullcontext())
    assert db.execute('SELECT phase FROM objects').fetchone()[0] == 'complete'
    sleep.assert_awaited_once_with(10)
    repair.assert_not_awaited()
    storage.remove_object.assert_called_once_with(migration.settings.minio_bucket, old)
    db.close()
