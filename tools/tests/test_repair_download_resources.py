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
