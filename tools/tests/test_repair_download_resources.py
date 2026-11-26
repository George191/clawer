import pytest
from tools.repair_download_resources import invalid_reason, reset_operation


@pytest.mark.parametrize('body', [b'<html>login.zscaler.net</html>', b'Zscaler Directory Authentication'])
def test_zscaler_is_rejected_regardless_of_extension(body):
    assert invalid_reason('news/source/record/file.html',body)=='Zscaler authentication page'


def test_binary_resource_html_and_explicit_block_pages_are_rejected():
    assert invalid_reason('news/source/id/file.pdf',b'<!DOCTYPE html><html>Login</html>')
    assert invalid_reason('news/source/id/file.html',b'<html><title>Access Denied</title>')
    assert invalid_reason('news/source/id/file.html',b'<html><title>News</title>') is None
    assert invalid_reason('news/source/id/file.pdf',b'%PDF-1.7 content') is None


def test_reset_removes_only_affected_completion_and_uses_generation_guards():
    manifest=[{'asset_key':'assets.cover','fingerprint':'bad'},{'asset_key':'assets.video','fingerprint':'good'}]
    doc={'_id':1,'_meta':{'download_status':'downloaded','downloaded_inputs':manifest,'download_version':3,'write_token':'token'},
         'assets':{'cover':'news/source/id/bad.jpg','video':'news/source/id/good.mp4'}}
    query,update=reset_operation(doc,{'assets.cover':'Zscaler authentication page'})
    assert query['assets.cover']==doc['assets']['cover']
    assert query['_meta.downloaded_inputs']==manifest
    assert query['_meta.download_version']==3 and query['_meta.write_token']=='token'
    assert update['$set']['_meta.downloaded_inputs']==manifest[1:]
    assert update['$set']['_meta.download_status']=='pending'
    assert 'assets.cover' in update['$unset'] and 'assets.video' not in update['$unset']
    assert '_meta.download_plan' not in update['$unset']
    assert update['$inc']=={'_meta.download_version':1}
    assert doc['_meta']['downloaded_inputs']==manifest


def test_active_download_is_not_reset():
    with pytest.raises(RuntimeError,match='Active download'):
        reset_operation({'_id':1,'_meta':{'download_status':'downloading'}},{'assets.file':'bad'})


def test_unsatisfiable_range_is_empty_only_after_size_confirmation():
    from unittest.mock import Mock
    from minio.error import S3Error
    from tools.repair_download_resources import inspect
    client=Mock();error=S3Error(response=None,code='InvalidRange',message='range',resource=None,request_id=None,host_id=None)
    client.get_object.side_effect=error
    client.stat_object.return_value.size=0
    assert inspect(client,'news/source/id/file.jpg')=='Stored resource is empty'
    client.stat_object.return_value.size=1
    with pytest.raises(S3Error):inspect(client,'news/source/id/file.jpg')


def test_download_producer_rejects_empty_payload():
    from unittest.mock import Mock
    from app.downloader.adapters.base import DownloadItem,validate_response
    from app.base.http import DownloadError
    response=Mock(status_code=200);response.read_prefix.return_value=b''
    with pytest.raises(DownloadError,match='Empty download response'):
        validate_response(DownloadItem('https://example.test/a.jpg','a.jpg','assets.cover'),response)


def test_resume_after_reset_commit_requires_replacement_object_check(tmp_path,monkeypatch):
    import json,sqlite3
    from unittest.mock import MagicMock
    from tools import repair_download_resources as repair
    db=sqlite3.connect(tmp_path/'audit.sqlite')
    db.execute('CREATE TABLE repairs(collection,identity,paths,reasons,state)')
    db.execute('INSERT INTO repairs VALUES(?,?,?,?,?)',('source','1',json.dumps({'assets.file':'news/source/id/bad.pdf'}),'{}','planned'));db.commit()
    mongo=MagicMock()
    mongo.__getitem__.return_value.find_one.return_value={'_meta':{'download_status':'downloaded'},'assets':{'file':'news/source/id/good.pdf'}}
    calls=[]
    def inspect(client,key):calls.append(key)
    monkeypatch.setattr(repair,'inspect',inspect)
    repair.verify_repairs(db,mongo,object())
    assert calls==['news/source/id/good.pdf']
    assert db.execute('SELECT state FROM repairs').fetchone()[0]=='verified'
    db.close()
