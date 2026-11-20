import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from tools import migrate_twitter_residuals as residuals


@pytest.mark.parametrize('referenced', [True, False])
async def test_residual_old_key_removed_only_after_copy_and_reference_check(referenced):
    rid = 'a' * 32
    old = f'social_media/twitter/{rid}/old.jpg'
    new = f'account/twitter/{rid}/' + 'b' * 64 + '.jpg'
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE twitter_residuals(old TEXT,new TEXT,phase TEXT,error TEXT)')
    db.execute('INSERT INTO twitter_residuals(old,phase) VALUES(?,"planned")', (old,))
    route = {'data_type': 'account', 'collection_name': 'account_twitter'}
    doc = {'id': '123', '_meta': {'record_id': rid}, 'avatar_url': new}
    collection = Mock(find_one=Mock(return_value=doc))
    pg = SimpleNamespace(fetch_all=AsyncMock(side_effect=[
        [{'raw_data': {'avatar_url': old if referenced else new}}], [],
    ]))
    client = Mock()
    with patch.object(residuals.migration, 'source_documents', return_value=[(route, doc)]), \
            patch.object(residuals.migration, 'copy_object', return_value=new) as copy:
        if referenced:
            with pytest.raises(RuntimeError, match='current database reference'):
                await residuals.rename_one(db, client, {'account_twitter': collection}, [], pg, old, rid, None)
            client.remove_object.assert_not_called()
            assert db.execute('SELECT phase FROM twitter_residuals').fetchone()[0] == 'copied'
        else:
            await residuals.rename_one(db, client, {'account_twitter': collection}, [], pg, old, rid, None)
            client.stat_object.assert_called_once_with(residuals.migration.settings.minio_bucket, new)
            client.remove_object.assert_called_once_with(residuals.migration.settings.minio_bucket, old)
            assert db.execute('SELECT phase FROM twitter_residuals').fetchone()[0] == 'complete'
        copy.assert_called_once_with(client, old, 'account/twitter', rid)
    db.close()
