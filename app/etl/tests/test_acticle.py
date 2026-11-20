from app.etl.normalizers.acticle import normalize_acticle
from app.etl.schema.catalogs import CATALOG_ODS_COLUMNS


def test_article_keeps_summary_without_creating_html_copy():
    result = normalize_acticle({'summary': 'Article summary', 'summary_html': 'Obsolete copy'})
    assert result['summary'] == 'Article summary'
    assert 'summary_html' not in result
    assert 'summary_html' not in CATALOG_ODS_COLUMNS['acticle']
    assert 'summary_html' in CATALOG_ODS_COLUMNS['news']


def test_article_cover_uses_downloaded_asset():
    result = normalize_acticle({
        'cover': 'https://mmbiz.qpic.cn/original.jpg',
        'assets': {'cover': 'acticle/acticle_wechat/record/cover.jpg'},
    })
    assert result['cover'] == 'acticle/acticle_wechat/record/cover.jpg'
