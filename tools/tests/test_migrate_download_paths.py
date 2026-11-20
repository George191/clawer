import hashlib
import pytest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools import migrate_download_paths as migration


def test_verification_requires_the_correct_resource_column():
    key = 'tweet/twitter/record/thumbnail.jpg'
    assert not migration.resource_references_match(
        {'thumbnail': key}, {'thumbnail': None, 'extra': {'thumbnail': key}}, [key])


def test_resource_json_verification_ignores_unrelated_delivery_metadata():
    import json
    key = 'tweet/twitter/record/thumbnail.jpg'
    expected = {'extra': json.dumps({'assets': [{'thumbnail_url': key}]})}
    actual = {'extra': {'assets': [{'thumbnail_url': key}], '_kafka_meta': {'offset': 100}}}
    assert migration.resource_references_match(expected, actual, [key])
    actual['extra']['assets'][0]['thumbnail_url'] = None
    assert not migration.resource_references_match(expected, actual, [key])


@pytest.mark.parametrize('prefix', ['social_media/twitter', 'twitter/twitter', 'tweet/twitter'])
def test_media_migration_uses_source_route_prefix(tmp_path, prefix):
    db = migration.journal(tmp_path / 'migration.sqlite')
    record_id = 'a' * 32
    old = f'{prefix}/{record_id}/video.mp4.mp4'
    new = f'tweet/twitter/{record_id}/' + 'b' * 64 + '.mp4'
    db.execute('INSERT INTO objects(old,prefix,record_id,shard) VALUES(?,?,?,?)',
               (old, prefix, record_id, int(record_id, 16) % 6))
    db.commit()
    route = {'collection_name': 'tweet_twitter', 'template_name': 'twitter', 'data_type': 'tweet'}
    doc = {'_id': 1, 'assets': {'media': {'0': {'url': old}}}}
    mongo = Mock()
    mongo.__getitem__ = Mock(return_value=Mock())
    mongo['tweet_twitter'].update_one.return_value.matched_count = 1
    with patch.object(migration, 'source_documents', return_value=[(route, doc)]), \
            patch.object(migration, 'copy_object', return_value=new) as copy:
        migration.apply_group(db, Mock(), mongo, [route], prefix, record_id)
    assert copy.call_args.args[2] == 'tweet/twitter'
    assert mongo['tweet_twitter'].update_one.call_args.args[1]['$set']['assets.media.0.url'] == new
    assert db.execute('SELECT new,phase FROM objects').fetchone() == (new, 'source_updated')
    db.close()


def test_copy_flattens_duplicate_extension_and_checks_published_bytes():
    payload = b'video fixture'
    digest = hashlib.sha256(payload).hexdigest()
    client = Mock()
    client.stat_object.return_value = SimpleNamespace(size=len(payload), etag='same-content', content_type='application/octet-stream')
    response = client.get_object.return_value
    response.stream.return_value = [payload]
    old = 'tweet/twitter/' + 'a' * 32 + '/' + 'b' * 64 + '/movie.mp4.mp4'
    new = migration.copy_object(client, old, 'tweet/twitter', 'a' * 32)
    assert new == 'tweet/twitter/' + 'a' * 32 + '/' + digest + '.mp4'
    assert client.fput_object.call_args.args[1] == new
    response.close.assert_called_once()
    response.release_conn.assert_called_once()


def test_copy_fails_if_destination_content_differs():
    client = Mock()
    client.stat_object.side_effect = [SimpleNamespace(size=3, etag='old', content_type='application/octet-stream'), SimpleNamespace(size=3, etag='new', content_type='application/octet-stream')]
    client.get_object.return_value.stream.side_effect = [[b'old'], [b'bad']]
    import pytest
    with pytest.raises(ValueError, match='verification failed'):
        migration.copy_object(client, 'news/source/' + 'a' * 32 + '/file.pdf', 'news/source', 'a' * 32)


def test_copy_timeout_accepts_only_a_verified_destination():
    payload = b'video fixture'
    client = Mock()
    client.stat_object.return_value = SimpleNamespace(size=len(payload), etag='same-content', content_type='application/octet-stream')
    client.get_object.return_value.stream.return_value = [payload]
    client.fput_object.side_effect = migration.urllib3.exceptions.ReadTimeoutError(None, '/copy', 'lost response')
    new = migration.copy_object(client, 'tweet/twitter/' + 'a' * 32 + '/video.mp4.mp4', 'tweet/twitter', 'a' * 32)
    assert new.endswith('/' + hashlib.sha256(payload).hexdigest() + '.mp4')
    client.fput_object.assert_called_once()
    client.copy_object.assert_not_called()


def test_copy_timeout_without_destination_remains_an_explicit_failure():
    client = Mock()
    failure = migration.urllib3.exceptions.ReadTimeoutError(None, '/copy', 'lost response')
    missing = migration.S3Error(None, 'NoSuchKey', 'missing', None, None, None)
    client.stat_object.side_effect = [SimpleNamespace(size=3, etag='old', content_type='application/octet-stream'), missing]
    client.get_object.return_value.stream.return_value = [b'old']
    client.fput_object.side_effect = failure
    with pytest.raises(migration.urllib3.exceptions.ReadTimeoutError):
        migration.copy_object(client, 'tweet/twitter/' + 'a' * 32 + '/video.mp4', 'tweet/twitter', 'a' * 32)


def test_migration_uploads_exact_source_bytes_and_removes_transfer_buffer():
    from pathlib import Path
    payload = b'complete source bytes'
    client = Mock()
    client.stat_object.return_value = SimpleNamespace(size=len(payload), etag='same', content_type='video/mp4')
    client.get_object.return_value.stream.return_value = [payload[:5], payload[5:]]
    paths = []
    def upload(bucket, key, path, **kwargs):
        paths.append(Path(path))
        assert paths[-1].read_bytes() == payload
    client.fput_object.side_effect = upload
    migration.copy_object(client, 'tweet/twitter/' + 'a' * 32 + '/video.mp4.mp4', 'tweet/twitter', 'a' * 32)
    assert paths and not paths[0].exists()
    client.copy_object.assert_not_called()


def test_incomplete_source_is_rejected_before_upload():
    client = Mock()
    client.stat_object.return_value = SimpleNamespace(size=10, etag='source')
    client.get_object.return_value.stream.return_value = [b'short']
    with pytest.raises(ValueError, match='Source object size verification failed'):
        migration.copy_object(client, 'tweet/twitter/' + 'a' * 32 + '/video.mp4', 'tweet/twitter', 'a' * 32)
    client.fput_object.assert_not_called()


def test_changed_source_content_is_not_relabelled_as_a_successful_migration():
    client = Mock()
    client.stat_object.return_value = SimpleNamespace(size=4, etag='source')
    client.get_object.return_value.stream.return_value = [b'file']
    old = 'tweet/twitter/' + 'a' * 32 + '/' + 'b' * 64 + '/' + 'c' * 64 + '/video.mp4'
    with pytest.raises(ValueError, match='Source object hash verification failed'):
        migration.copy_object(client, old, 'tweet/twitter', 'a' * 32)
    client.fput_object.assert_not_called()


def test_resume_after_mongo_commit_still_requires_downstream_verification(tmp_path):
    db = migration.journal(tmp_path / 'migration.sqlite')
    record_id = 'a' * 32
    old, new = 'news/source/' + record_id + '/file.pdf', 'news/source/' + record_id + '/' + 'b' * 64 + '.pdf'
    db.execute('INSERT INTO objects(old,prefix,record_id,shard,new) VALUES(?,?,?,?,?)', (old,'news/source',record_id,int(record_id,16)%6,new))
    db.commit()
    doc = {'_id':1,'assets':{'document':new}}
    with patch.object(migration,'source_documents',return_value=[({'collection_name':'news_source'},doc)]):
        migration.apply_group(db,Mock(),Mock(),[],'news/source',record_id)
    assert db.execute('SELECT phase FROM objects').fetchone()[0] == 'source_updated'
    db.close()


def test_partition_ownership_is_disjoint_and_complete():
    records = [f'{n:032x}' for n in range(3000)]
    shards = [{r for r in records if int(r,16)%migration.WORKERS==worker} for worker in range(migration.WORKERS)]
    assert len(set.union(*shards)) == len(records)
    assert sum(len(shard) for shard in shards) == len(records)
    assert all(len(a & b)==0 for i,a in enumerate(shards) for b in shards[i+1:])


def test_active_download_claim_is_checked_before_transferring_bytes(tmp_path):
    db = migration.journal(tmp_path / 'journal.sqlite')
    doc = {'_meta': {'download_status': 'downloading'}}
    client = Mock()
    with patch.object(migration, 'source_documents', return_value=[({}, doc)]):
        with pytest.raises(migration.ConcurrentSourceUpdate):
            migration.apply_group(db, client, None, [], 'tweet/twitter', 'a' * 32)
    client.get_object.assert_not_called()
    client.fput_object.assert_not_called()
    db.close()

def test_indexed_inventory_resumes_without_relisting_sources(tmp_path):
    from datetime import datetime, timezone
    from unittest.mock import MagicMock
    record_id = 'a' * 32
    old = 'news/source/' + record_id + '/image.jpg.jpg'
    client = MagicMock()
    collection = client.__getitem__.return_value.__getitem__.return_value
    collection.index_information.return_value = {'downloads': {'key': [
        ('_meta.download_status',1),('_meta.updated_at',1),('_id',1)]}}
    doc = {'_id':'row1','_meta':{'record_id':record_id,'updated_at':datetime(2026,1,1,tzinfo=timezone.utc)},'assets':{'image':old,'missing':{'status_code':404,'url':'','message':'Source file returned HTTP 404; the file no longer exists.'}}}
    def find(query, projection):
        assert '$lte' not in query.get('_meta.updated_at', {})  # Active source updates remain discoverable.
        if '$or' in query:assert query['_meta.updated_at']=={'$gte':doc['_meta']['updated_at']}
        cursor = MagicMock()
        cursor.sort.return_value.hint.return_value.limit.return_value.max_time_ms.return_value = (
            [doc] if query['_meta.download_status']=='downloaded' and '$or' not in query else [])
        return cursor
    collection.find.side_effect = find
    async def routes():
        return [{'template_name':'source','collection_name':'news_source','data_type':'news'}]
    path = tmp_path / 'journal.sqlite'
    with patch.object(migration,'MongoClient',return_value=client), patch.object(migration,'routes',routes):
        migration.inventory(path)
        calls = collection.find.call_count
        migration.inventory(path)
    assert collection.find.call_count == calls
    db = migration.journal(path)
    assert db.execute('SELECT old,phase FROM objects').fetchall() == [(old,'planned')]
    assert db.execute('SELECT done FROM progress WHERE prefix="__inventory__"').fetchone()[0] == 1
    db.close()

@pytest.mark.asyncio
async def test_reference_repair_preserves_delivery_positions_and_updates_only_path_columns(tmp_path):
    from contextlib import asynccontextmanager
    from datetime import datetime, timezone
    from unittest.mock import AsyncMock
    import json
    record_id='a'*32
    old=f'news/source/{record_id}/file.jpg.jpg'
    new=f'news/source/{record_id}/'+ 'b'*64 + '.jpg'
    db=migration.journal(tmp_path/'journal.sqlite')
    db.execute('INSERT INTO objects(old,prefix,record_id,shard,new,phase) VALUES(?,?,?,?,?,?)',(old,'news/source',record_id,0,new,'source_updated'));db.commit()
    route={'collection_name':'news_source','data_type':'news','template_name':'source'}
    doc={'_id':1,'_meta':{'record_id':record_id,'updated_at':datetime(2026,1,2,tzinfo=timezone.utc)},'title':'source title','assets':{'image':new}}
    calls=[]
    class Result:
        rowcount=1
        def __init__(self,value=None):self.value=value
        def mappings(self):return self
        def first(self):return self.value
        def all(self):return self.value
    async def execute(sql,params=None):
        sql=str(sql);calls.append((sql,params))
        if sql.startswith('SELECT raw_data'):return Result({'raw_data':{'_meta':{'updated_at':'2026-01-01T00:00:00Z'},'_kafka_meta':{'kafka_offset':123}}})
        if 'information_schema.columns' in sql:return Result([{'column_name':'url','data_type':'text'},{'column_name':'images','data_type':'jsonb'}])
        return Result()
    @asynccontextmanager
    async def session():yield SimpleNamespace(execute=execute)
    module=SimpleNamespace(NORMALIZERS={'news':lambda doc:{'url':new,'images':[{'url':new}],'title':'source title'}})
    with patch.object(migration,'source_documents',return_value=[(route,doc)]),patch.object(migration,'get_pg_client',return_value=SimpleNamespace(session=session)),patch('app.adapters.runtime.get_source_module',AsyncMock(return_value=module)):
        await migration.repair_references(db,Mock(),[], 'news/source',record_id)
    rds=[(sql,p) for sql,p in calls if sql.startswith('UPDATE ts_rds')][0]
    assert json.loads(rds[1]['raw'])['_kafka_meta']=={'kafka_offset':123}
    assert 'kafka_offset=' not in rds[0]
    ods=[sql for sql,p in calls if sql.startswith('UPDATE ts_ods')][0]
    assert '"url"=' in ods and '"images"=CAST(' in ods
    assert 'title=' not in ods
    assert any('FOR UPDATE' in sql for sql,p in calls)
    db.close()
