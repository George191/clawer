import hashlib
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools import migrate_download_paths as migration


def test_copy_flattens_duplicate_extension_and_checks_published_bytes():
    payload = b'video fixture'
    digest = hashlib.sha256(payload).hexdigest()
    client = Mock()
    client.stat_object.return_value = SimpleNamespace(size=len(payload), etag='same-content')
    response = client.get_object.return_value
    response.stream.return_value = [payload]
    old = 'tweet/twitter/' + 'a' * 32 + '/' + 'b' * 64 + '/movie.mp4.mp4'
    new = migration.copy_object(client, old, 'tweet/twitter', 'a' * 32)
    assert new == 'tweet/twitter/' + 'a' * 32 + '/' + digest + '.mp4'
    assert client.copy_object.call_args.args[1] == new
    response.close.assert_called_once()
    response.release_conn.assert_called_once()


def test_copy_fails_if_destination_content_differs():
    client = Mock()
    client.stat_object.side_effect = [SimpleNamespace(size=3, etag='old'), SimpleNamespace(size=3, etag='new')]
    client.get_object.return_value.stream.side_effect = [[b'old'], [b'bad']]
    import pytest
    with pytest.raises(ValueError, match='verification failed'):
        migration.copy_object(client, 'news/source/' + 'a' * 32 + '/file.pdf', 'news/source', 'a' * 32)


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
