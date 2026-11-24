import hashlib
import pytest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools import migrate_download_paths as migration


def test_missing_asset_metadata_is_not_a_resource_path():
    marker={'url':'','description':'Source file returned HTTP 404; the file no longer exists.','status_code':404}
    assets={'attachments':{'0':{**marker,'url':dict(marker)}}}
    assert all(not value for _,value in migration.strings(assets))


def test_asset_url_is_not_lost_when_missing_metadata_is_stale():
    key='news/source/'+'a'*32+'/file.pdf'
    assets={'attachments':{'0':{'url':key,'description':'missing','status_code':404}}}
    assert list(migration.strings(assets))==[('attachments.0.url',key)]


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

@pytest.mark.parametrize('legacy_owner', ['current','renamed','shared'])
def test_indexed_inventory_resumes_without_relisting_sources(tmp_path, legacy_owner):
    from datetime import datetime, timezone
    from unittest.mock import MagicMock
    record_id = 'a' * 32
    old = {'current':'news/source/'+record_id+'/image.jpg.jpg',
           'renamed':'news/old_source/'+'b'*32+'/image.jpg.jpg',
           'shared':'news/satellite_today/_shared_wayback/shared.jpeg'}[legacy_owner]
    client = MagicMock()
    collection = client.__getitem__.return_value.__getitem__.return_value
    collection.index_information.return_value = {'downloads': {'key': [
        ('_meta.download_status',1),('_meta.updated_at',1),('_id',1)]}}
    doc = {'_id':'row1','_meta':{'record_id':record_id,'updated_at':datetime(2026,1,1,tzinfo=timezone.utc)},'assets':{'image':old,'missing':{'status_code':404,'url':'','message':'Source file returned HTTP 404; the file no longer exists.'}}}
    cursors = []
    def find(query, projection):
        boundary = query.get('_meta.updated_at')
        assert '$or' not in query
        if isinstance(boundary,dict):assert boundary=={'$gt':doc['_meta']['updated_at']}
        elif boundary is not None:
            assert boundary==doc['_meta']['updated_at']
            assert query['_id']=={'$gt':doc['_id']}
        cursor = MagicMock()
        cursors.append(cursor)
        cursor.sort.return_value.hint.return_value.batch_size.return_value.limit.return_value.max_time_ms.return_value = (
            [doc] if query['_meta.download_status']=='downloaded' and boundary is None else [])
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
    # Exercise the actual driver's command builder with the inventory's captured
    # arguments: no server cursor may survive this page, even for a short batch.
    from pymongo.message import _gen_find_command
    from pymongo.read_concern import ReadConcern
    page = cursors[0].sort.return_value.hint.return_value
    size = page.batch_size.call_args.args[0]
    limit = page.batch_size.return_value.limit.call_args.args[0]
    command = _gen_find_command('news_source', {}, {'assets':1}, 0, limit, size, 0, ReadConcern())
    assert command['singleBatch'] is True
    assert command['limit'] == command['batchSize'] == migration.SOURCE_PAGE_SIZE
    assert 0 < migration.SOURCE_PAGE_SIZE <= migration.BATCH
    db = migration.journal(path)
    assert db.execute('SELECT old,phase FROM objects').fetchall() == [(old,'planned')]
    assert db.execute('SELECT prefix,record_id,shard FROM objects').fetchone()==('news/source',record_id,int(record_id,16)%migration.WORKERS)
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


def test_existing_journal_upgrade_preserves_rows_checkpoints_and_errors(tmp_path):
    import sqlite3
    path=tmp_path/'journal.sqlite';db=sqlite3.connect(path)
    db.execute('CREATE TABLE objects (old TEXT PRIMARY KEY,prefix TEXT,record_id TEXT,shard INTEGER,new TEXT,phase TEXT NOT NULL DEFAULT "planned",error TEXT)')
    original=('old/key','news/source','a'*32,4,'new/key','verified','original error')
    db.execute('INSERT INTO objects VALUES(?,?,?,?,?,?,?)',original)
    db.execute('CREATE TABLE progress (prefix TEXT PRIMARY KEY,last_key TEXT NOT NULL,done INTEGER NOT NULL DEFAULT 0)')
    db.execute('INSERT INTO progress VALUES("source","checkpoint",1)')
    db.execute('CREATE TABLE error_history (id INTEGER PRIMARY KEY,old TEXT NOT NULL,error TEXT NOT NULL,recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')
    db.execute('INSERT INTO error_history VALUES(1,"old/key","prior failure","2026-11-23")');db.commit();db.close()
    db=migration.journal(path)
    assert db.execute('SELECT * FROM objects').fetchone()==original
    assert db.execute('SELECT * FROM progress').fetchone()==('source','checkpoint',1)
    assert db.execute('SELECT * FROM error_history').fetchone()==(1,'old/key','prior failure','2026-11-23',None,None)
    db.execute('INSERT INTO objects(old,prefix,record_id,shard) VALUES(?,?,?,?)',('old/key','news/source','b'*32,1));db.commit();db.close()
    db=migration.journal(path)
    assert db.execute('SELECT count(*) FROM objects').fetchone()[0]==2
    db.close()


def test_source_record_lookup_uses_owner_index(tmp_path):
    db=migration.journal(tmp_path/'journal.sqlite')
    plan=db.execute('EXPLAIN QUERY PLAN SELECT old,new,phase FROM objects WHERE prefix=? AND record_id=?',('news/source','a'*32)).fetchall()
    assert any('SEARCH objects USING INDEX objects_owner' in row[3] for row in plan)
    assert not any('SCAN objects' in row[3] for row in plan)
    plan=db.execute('EXPLAIN QUERY PLAN SELECT old,error FROM objects WHERE shard=? AND phase=? AND error IS NOT NULL',(0,'planned')).fetchall()
    assert any('USING INDEX objects_errors' in row[3] for row in plan)
    db.close()


def test_shared_file_reference_updates_only_its_current_owner(tmp_path):
    from unittest.mock import MagicMock
    db=migration.journal(tmp_path/'journal.sqlite')
    old='news/satellite_today/_shared_wayback/shared.jpg'
    route={'collection_name':'news_satellite_today','template_name':'satellite_today','data_type':'news'}
    owners=['a'*32,'b'*32]
    for identity in owners:
        db.execute('INSERT INTO objects(old,prefix,record_id,shard) VALUES(?,?,?,?)',(old,'news/satellite_today',identity,int(identity,16)%6))
    db.commit();mongo=MagicMock();mongo.__getitem__.return_value.update_one.return_value.matched_count=1
    def documents(mongo,registered,prefix,identity):return [(route,{'_id':identity,'assets':{'image':old}})]
    def copy(client,key,prefix,identity):return prefix+'/'+identity+'/content.jpg'
    with patch.object(migration,'source_documents',side_effect=documents),patch.object(migration,'copy_object',side_effect=copy):
        migration.apply_group(db,Mock(),mongo,[route],'news/satellite_today',owners[0])
        assert db.execute('SELECT phase,new FROM objects WHERE record_id=?',(owners[1],)).fetchone()==('planned',None)
        migration.apply_group(db,Mock(),mongo,[route],'news/satellite_today',owners[1])
    rows=db.execute('SELECT record_id,new,phase FROM objects ORDER BY record_id').fetchall()
    assert rows==[(identity,'news/satellite_today/'+identity+'/content.jpg','source_updated') for identity in owners]
    db.close()
