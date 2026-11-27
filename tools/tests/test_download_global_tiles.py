import json
import ssl
from urllib.error import URLError
from pathlib import Path
from unittest.mock import patch

import pytest

from tools import download_global_tiles as tiles


JPEG = b'\xff\xd8\xfftest\xff\xd9'
PNG = b'\x89PNG\r\n\x1a\n' + b'test' + b'IEND\xaeB`\x82'


def test_tls_outage_recovers_after_four_failed_attempts(tmp_path):
    error = URLError(ssl.SSLEOFError('UNEXPECTED_EOF_WHILE_READING'))
    with patch.object(tiles, 'fetch', side_effect=[error] * 4 + [JPEG]) as fetch, \
            patch.object(tiles.time, 'sleep') as sleep:
        state = tiles.worker(str(tmp_path), 'satellite', 0, 0)
    assert fetch.call_count == 5
    assert [call.args[0] for call in sleep.call_args_list] == [5, 10, 20, 40]
    assert state['done'] and state['tiles'] == 1 and state['error'] is None


def test_persistent_tls_failure_preserves_checkpoint_for_resume(tmp_path):
    error = URLError(ssl.SSLEOFError('UNEXPECTED_EOF_WHILE_READING'))
    with patch.object(tiles, 'fetch', side_effect=[JPEG] + [error] * 8) as fetch, \
            patch.object(tiles.time, 'sleep') as sleep:
        with pytest.raises(URLError):
            tiles.worker(str(tmp_path), 'satellite', 2, 0)
    assert fetch.call_count == 9
    assert [call.args[0] for call in sleep.call_args_list] == [5, 10, 20, 40, 60, 60, 60]
    checkpoint = tmp_path / 'output' / 'satellite-2-0.json'
    state = json.loads(checkpoint.read_text())
    assert state['next'] == 6 and state['tiles'] == 1 and not state['done']
    assert 'UNEXPECTED_EOF' in state['error']
    target = tmp_path / 'satellite' / '2' / '0' / '0.jpg'
    before = target.stat().st_mtime_ns
    with patch.object(tiles, 'fetch', return_value=JPEG) as fetch:
        state = tiles.worker(str(tmp_path), 'satellite', 2, 0)
    assert fetch.call_count == 2
    assert state['done'] and state['tiles'] == 3 and state['error'] is None
    assert target.stat().st_mtime_ns == before


def test_six_workers_cover_level_once_and_resume_without_network(tmp_path):
    with patch.object(tiles, 'fetch', return_value=JPEG) as fetch:
        for worker in range(6):
            tiles.worker(str(tmp_path), 'satellite', 2, worker)
        assert fetch.call_count == 16
    assert len(list((tmp_path / 'satellite').rglob('*.jpg'))) == 16
    states = [json.loads(p.read_text()) for p in (tmp_path / 'output').glob('*.json')]
    assert sum(s['tiles'] for s in states) == 16
    assert all(s['done'] for s in states)
    with patch.object(tiles, 'fetch', side_effect=AssertionError('Unexpected redownload')):
        for worker in range(6):
            tiles.worker(str(tmp_path), 'satellite', 2, worker)


def test_invalid_response_never_publishes_or_advances_checkpoint(tmp_path):
    with patch.object(tiles, 'fetch', return_value=b'<html>error</html>'), patch.object(tiles.time, 'sleep'):
        with pytest.raises(ValueError, match='Invalid image'):
            tiles.worker(str(tmp_path), 'overlay', 0, 0)
    state = json.loads((tmp_path / 'output' / 'overlay-0-0.json').read_text())
    assert state['next'] == 0 and state['tiles'] == 0 and not state['done']
    assert state['error']
    assert not list((tmp_path / 'overlay').rglob('*.png'))


def test_file_published_before_interruption_is_reused(tmp_path):
    target = tmp_path / 'satellite' / '0' / '0' / '0.jpg'
    target.parent.mkdir(parents=True)
    target.write_bytes(JPEG)
    with patch.object(tiles, 'fetch', side_effect=AssertionError('Unexpected redownload')):
        state = tiles.worker(str(tmp_path), 'satellite', 0, 0)
    assert state['tiles'] == 1 and state['done']


def test_pg_report_failure_does_not_hide_independent_tile_status(tmp_path, capsys):
    import asyncio
    from unittest.mock import AsyncMock
    from output.source_dedup_20261109 import progress_table as progress
    with patch.object(progress, 'HERE', tmp_path), \
            patch.object(progress, 'collect', AsyncMock(side_effect=RuntimeError('PG connections full'))), \
            patch.object(progress, 'collect_tiles', AsyncMock(return_value={'verified': False, 'error_class': 'TileProbeError'})):
        with pytest.raises(SystemExit) as stopped:
            asyncio.run(progress.main(as_markdown=True))
    assert stopped.value.code == 1
    output = capsys.readouterr().out
    assert 'PG connections full' in output and 'TileProbeError' in output


def test_adding_roads_preserves_existing_satellite_without_fetching_again(tmp_path):
    import hashlib
    with patch.object(tiles, 'fetch', return_value=JPEG):
        for worker in range(6):
            tiles.worker(str(tmp_path), 'satellite', 2, worker)
    before = {str(p): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
              for p in tmp_path.rglob('*') if p.is_file()}
    with patch.object(tiles, 'fetch', return_value=PNG) as fetch:
        for source in tiles.SOURCES:
            for worker in range(6):
                tiles.worker(str(tmp_path), source, 2, worker)
    assert fetch.call_count == 16
    assert all('?lyrs=h&' in call.args[0] for call in fetch.call_args_list)
    from urllib.parse import parse_qs, urlsplit
    assert all(parse_qs(urlsplit(call.args[0]).query)['apistyle'] == [tiles.ROAD_STYLE]
               for call in fetch.call_args_list)
    assert before == {p: (hashlib.sha256(Path(p).read_bytes()).hexdigest(), Path(p).stat().st_mtime_ns)
                      for p in before}
    assert len(list((tmp_path / 'overlay').rglob('*.png'))) == 16


def test_electronic_map_is_rejected_before_any_file_or_network_work(tmp_path):
    with patch.object(tiles, 'fetch', side_effect=AssertionError('Unexpected electronic map fetch')):
        with pytest.raises(ValueError, match='Unsupported tile source: roadmap'):
            tiles.worker(str(tmp_path), 'roadmap', 0, 0)
    assert not list(tmp_path.iterdir())
    assert tiles.SOURCES == {'satellite': 's', 'overlay': 'h'}
